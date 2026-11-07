"""End-to-end report generation in two phases.

1. Build the context: collect -> preprocess -> group (+ merge stories) -> score -> select.
2. Generate the form: the LLM writes a tagline and summary for each selected story.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from .. import metrics
from ..config import AppConfig
from ..connectors import Connector, build_connector
from ..llm import LLMClient
from ..models import Article, Report, ReportEntry
from .grouping import group_articles
from .preprocess import embed_articles, preprocess
from .scoring import score_topics
from .selection import Story, select_topics
from .summarize import summarize_topic

log = logging.getLogger(__name__)

USER_AGENT = "PWSNews/0.1 (+https://github.com/runyanjake/news)"
FETCH_TIMEOUT_SECONDS = 20
LLM_CONCURRENCY = 2

async def collect(connectors: list[Connector]) -> list[list[Article]]:
    """Raw items per source; failed sources contribute nothing."""
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(*(c.fetch(client) for c in connectors), return_exceptions=True)

    per_source, failed = [], []
    for connector, result in zip(connectors, results, strict=True):
        if isinstance(result, Exception):
            log.warning("Collect: source %s failed: %s", connector.source.id, result)
            failed.append(connector.source.id)
            metrics.SOURCE_FETCHES.labels(connector.source.id, "failure").inc()
            metrics.SOURCE_ITEMS.labels(connector.source.id).set(0)
            continue
        metrics.SOURCE_FETCHES.labels(connector.source.id, "success").inc()
        metrics.SOURCE_ITEMS.labels(connector.source.id).set(len(result))
        per_source.append(result)
    log.info(
        "Collect: %d items from %d of %d sources%s",
        sum(map(len, per_source)), len(per_source), len(connectors),
        f" (failed: {', '.join(failed)})" if failed else "",
    )
    return per_source


async def build_context(
    config: AppConfig, llm: LLMClient | None, connectors: list[Connector], now: datetime
) -> list[Story]:
    articles = preprocess(await collect(connectors), config.pipeline, now)
    await embed_articles(articles, llm, config.grouping.embeddings)
    topics = await group_articles(articles, config.grouping, llm)
    ranked = score_topics(topics, config.scoring, now)
    return select_topics(ranked, config.report)


async def write_entries(stories: list[Story], llm: LLMClient | None) -> list[ReportEntry]:
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    async def summarize(topic, top):
        async with semaphore:
            return await summarize_topic(topic, top, llm)

    entries = list(await asyncio.gather(*(summarize(t, top) for t, top in stories)))
    fallback = sum(1 for e in entries if not e.ai_generated)
    metrics.REPORT_ENTRIES.labels("llm").set(len(entries) - fallback)
    metrics.REPORT_ENTRIES.labels("fallback").set(fallback)
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
