"""End-to-end report generation: collect -> group -> score -> summarize."""

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx

from ..config import AppConfig, ReportConfig
from ..connectors import Connector, build_connector
from ..llm import LLMClient
from ..models import Article, Report, Topic
from .grouping import group_articles
from .scoring import best_per_source, score_topics
from .summarize import summarize_topic

log = logging.getLogger(__name__)

USER_AGENT = "PWSNews/0.1 (+https://github.com/runyanjake/news)"
FETCH_TIMEOUT_SECONDS = 20
LLM_CONCURRENCY = 2


async def collect(config: AppConfig, connectors: list[Connector], now: datetime) -> list[Article]:
    cutoff = now - timedelta(hours=config.pipeline.max_age_hours)
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS,headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(*(c.fetch(client) for c in connectors), return_exceptions=True)

    articles, seen = [], set()
    for connector, result in zip(connectors, results, strict=True):
        if isinstance(result, Exception):
            log.warning("Source %s failed: %s", connector.source.id, result)
            continue
        fresh = [a for a in result if a.published_at is None or a.published_at >= cutoff]
        for article in fresh[: config.pipeline.max_items_per_source]:
            if article.url not in seen:
                seen.add(article.url)
                articles.append(article)
    # Newest first, so any downstream truncation (e.g. LLM grouping) drops stale items.
    articles.sort(key=lambda a: a.published_at or cutoff, reverse=True)
    return articles


def select_topics(ranked: list[Topic], config: ReportConfig) -> list[tuple[Topic, list[Article]]]:
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

    articles = await collect(config, connectors, now)
    log.info("Collected %d articles from %d sources", len(articles), len(connectors))

    topics = await group_articles(articles, config.grouping, llm)
    per_entry = config.report.sources_per_entry
    ranked = score_topics(topics, config.scoring, now, per_entry)

    selected = select_topics(ranked, config.report)
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    async def summarize(topic, top):
        async with semaphore:
            return await summarize_topic(topic, top, llm)

    entries = await asyncio.gather(*(summarize(t, top) for t, top in selected))
    return Report(date=local_date, generated_at=now, entries=list(entries))
