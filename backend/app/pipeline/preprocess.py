"""Preprocess sources: trim and annotate raw articles before anything is built on them.

This step only limits what enters the context (stale items, per-source caps,
duplicate articles) and attaches derived fields the later stages share. It
never decides which articles are the same *story*; that is merge_stories.
"""

from datetime import datetime, timedelta

from ..config import PipelineConfig
from ..models import Article
from .text import canonical_url, tokens


def annotate(article: Article) -> Article:
    article.url_key = canonical_url(article.url)
    article.words = frozenset(tokens(article.title))
    return article


def preprocess(
    per_source: list[list[Article]], config: PipelineConfig, now: datetime
) -> list[Article]:
    """Fresh, capped, annotated articles with one copy per URL, newest first.

    When feeds carry the same article, the most reputable feed's copy is kept.
    """
    cutoff = now - timedelta(hours=config.max_age_hours)
    best: dict[str, Article] = {}
    for articles in per_source:
        fresh = [a for a in articles if a.published_at is None or a.published_at >= cutoff]
        for article in fresh[: config.max_items_per_source]:
            annotate(article)
            current = best.get(article.url_key)
            if current is None or article.reputation > current.reputation:
                best[article.url_key] = article
    # Newest first, so any downstream truncation (e.g. LLM grouping) drops stale items.
    return sorted(best.values(), key=lambda a: a.published_at or cutoff, reverse=True)
