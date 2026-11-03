"""End-to-end report generation in two phases.

1. Build the context: collect -> preprocess -> group (+ merge stories) -> score -> select.
2. Generate the form: the LLM writes a tagline and summary for each selected story.
"""

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime

import httpx

from ..config import AppConfig, ReportConfig
from ..connectors import Connector, build_connector
from ..llm import LLMClient
from ..models import Article, Report, ReportEntry, Topic
from .grouping import group_articles
from .preprocess import preprocess
from .scoring import best_per_source, score_topics
from .summarize import summarize_topic

log = logging.getLogger(__name__)

USER_AGENT = "PWSNews/0.1 (+https://github.com/runyanjake/news)"
FETCH_TIMEOUT_SECONDS = 20
LLM_CONCURRENCY = 2

Story = tuple[Topic, list[Article]]  # a ranked topic and the top sources shown for it


async def collect(connectors: list[Connector]) -> list[list[Article]]:
    """Raw items per source; failed sources contribute nothing."""
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(*(c.fetch(client) for c in connectors), return_exceptions=True)

    per_source = []
    for connector, result in zip(connectors, results, strict=True):
        if isinstance(result, Exception):
            log.warning("Source %s failed: %s", connector.source.id, result)
            continue
        per_source.append(result)
    return per_source


def select_topics(ranked: list[Topic], config: ReportConfig) -> list[Story]:
    """Best topics with their top sources, capping each section (the top source's first tag)."""
    cap = config.max_entries_per_tag
    selected, per_tag = [], Counter()
    for topic in ranked:
        top = best_per_source(topic)[: config.sources_per_entry]
        if len(top) < config.min_sources_per_entry:
            continue
        section = top[0].tags[0] if top[0].tags else None
        if section and cap is not None and per_tag[section] >= cap:
            continue
        per_tag[section] += 1
        selected.append((topic, top))
        if len(selected) == config.max_entries:
            break
    return selected


async def build_context(
    config: AppConfig, llm: LLMClient | None, connectors: list[Connector], now: datetime
) -> list[Story]:
    articles = preprocess(await collect(connectors), config.pipeline, now)
    log.info("Collected %d articles from %d sources", len(articles), len(connectors))

    topics = await group_articles(articles, config.grouping, llm)
    ranked = score_topics(topics, config.scoring, now, config.report.sources_per_entry)
    return select_topics(ranked, config.report)


async def write_entries(stories: list[Story], llm: LLMClient | None) -> list[ReportEntry]:
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    async def summarize(topic, top):
        async with semaphore:
            return await summarize_topic(topic, top, llm)

    return list(await asyncio.gather(*(summarize(t, top) for t, top in stories)))


async def generate_report(
    config: AppConfig,
    llm: LLMClient | None,
    local_date: str,
    connectors: list[Connector] | None = None,
    now: datetime | None = None,
) -> Report:
    now = now or datetime.now(UTC)
    if connectors is None:
        connectors = [build_connector(s) for s in config.sources if s.enabled]

    stories = await build_context(config, llm, connectors, now)
    entries = await write_entries(stories, llm)
    return Report(date=local_date, generated_at=now, entries=entries)
