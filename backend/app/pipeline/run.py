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

    per_source, failed = [], []
    for connector, result in zip(connectors, results, strict=True):
        if isinstance(result, Exception):
            log.warning("Collect: source %s failed: %s", connector.source.id, result)
            failed.append(connector.source.id)
            continue
        per_source.append(result)
    log.info(
        "Collect: %d items from %d of %d sources%s",
        sum(map(len, per_source)), len(per_source), len(connectors),
        f" (failed: {', '.join(failed)})" if failed else "",
    )
    return per_source


def select_topics(ranked: list[Topic], config: ReportConfig) -> list[Story]:
    """Best topics with their top sources, capping each section (the top source's first tag)."""
    cap = config.max_entries_per_tag
    selected, per_tag, thin, capped = [], Counter(), 0, Counter()
    for topic in ranked:
        top = best_per_source(topic)[: config.sources_per_entry]
        if len(top) < config.min_sources_per_entry:
            thin += 1
            continue
        section = top[0].tags[0] if top[0].tags else None
        if section and cap is not None and per_tag[section] >= cap:
            capped[section] += 1
            continue
        per_tag[section] += 1
        selected.append((topic, top))
        if len(selected) == config.max_entries:
            break
    skipped = [f"{thin} with fewer than {config.min_sources_per_entry} sources"] if thin else []
    if capped:
        by_tag = ", ".join(f"{tag} {n}" for tag, n in capped.most_common())
        skipped.append(f"{capped.total()} over the per-section cap of {cap} ({by_tag})")
    log.info(
        "Select: %d of %d stories%s",
        len(selected), len(ranked), f" (skipped {'; '.join(skipped)})" if skipped else "",
    )
    return selected


async def build_context(
    config: AppConfig, llm: LLMClient | None, connectors: list[Connector], now: datetime
) -> list[Story]:
    articles = preprocess(await collect(connectors), config.pipeline, now)
    topics = await group_articles(articles, config.grouping, llm)
    ranked = score_topics(topics, config.scoring, now, config.report.sources_per_entry)
    return select_topics(ranked, config.report)


async def write_entries(stories: list[Story], llm: LLMClient | None) -> list[ReportEntry]:
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    async def summarize(topic, top):
        async with semaphore:
            return await summarize_topic(topic, top, llm)

    entries = list(await asyncio.gather(*(summarize(t, top) for t, top in stories)))
    fallback = sum(1 for e in entries if not e.ai_generated)
    log.info("Write: %d entries (%d AI-written, %d fell back to the top source)",
             len(entries), len(entries) - fallback, fallback)
    return entries


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
    report = Report(
        date=local_date, generated_at=now, entries=entries, site_name=config.report.site_name
    )
    log.info("%s", describe_report(report))
    return report


def describe_report(report: Report) -> str:
    """Multi-line summary for the logs: the report title, then one bullet per story."""
    lines = [f'Generated report "{report.title}" ({len(report.entries)} stories)']
    for entry in report.entries:
        sources = len(entry.sources)
        lines.append(f"  - {entry.tagline} ({sources} source{'s' if sources != 1 else ''})")
    return "\n".join(lines)
