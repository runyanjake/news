"""Preprocess sources: trim and annotate raw articles before anything is built on them.

This step only limits what enters the context (stale items, repeats, per-source
caps, duplicate articles) and attaches derived fields the later stages share. It
never decides which articles are the same *story*; that is merge_stories.
"""

import logging
from datetime import datetime, timedelta

from ..config import PipelineConfig
from ..models import Article
from .text import canonical_url, tokens

log = logging.getLogger(__name__)


def annotate(article: Article) -> Article:
    article.url_key = canonical_url(article.url)
    article.words = frozenset(tokens(article.title))
    return article


def _unique_in_feed(articles: list[Article]) -> list[Article]:
    """Drop items a feed lists more than once (same link), keeping the first."""
    seen: dict[str, Article] = {}
    repeats = []
    for article in articles:
        if article.url_key in seen:
            repeats.append(article)
        else:
            seen[article.url_key] = article
    if repeats:
        log.info(
            "Preprocess: %s listed %d item(s) more than once: %s",
            repeats[0].source_name, len(repeats), "; ".join(f'"{a.title}"' for a in repeats),
        )
    return list(seen.values())


def preprocess(
    per_source: list[list[Article]], config: PipelineConfig, now: datetime
) -> list[Article]:
    """Fresh, capped, annotated articles with one copy per URL, newest first.

    Per feed: drop stale items, then repeats, then apply the cap (so repeats don't
    use up slots). Across feeds, the most reputable feed's copy of an article is kept.
    """
    cutoff = now - timedelta(hours=config.max_age_hours)
    counts = {"items": 0, "stale": 0, "repeated": 0, "over cap": 0, "duplicate": 0}
    copies: dict[str, list[Article]] = {}
    for articles in per_source:
        counts["items"] += len(articles)
        fresh = [annotate(a) for a in articles if a.published_at is None or a.published_at >= cutoff]
        unique = _unique_in_feed(fresh)
        kept = unique[: config.max_items_per_source]
        counts["stale"] += len(articles) - len(fresh)
        counts["repeated"] += len(fresh) - len(unique)
        counts["over cap"] += len(unique) - len(kept)
        for article in kept:
            copies.setdefault(article.url_key, []).append(article)

    kept = []
    for url_key, group in copies.items():
        best = max(group, key=lambda a: a.reputation)  # first feed wins ties
        if len(group) > 1:
            counts["duplicate"] += len(group) - 1
            log.info(
                "Preprocess: duplicate link %s from %s; kept %s",
                url_key, ", ".join(a.source_name for a in group), best.source_name,
            )
        kept.append(best)

    log.info(
        "Preprocess: %d items -> %d articles (dropped %d stale, %d repeated within a feed, "
        "%d over the per-source cap, %d duplicate links across feeds)",
        counts["items"], len(kept), counts["stale"], counts["repeated"], counts["over cap"],
        counts["duplicate"],
    )
    # Newest first, so any downstream truncation (e.g. LLM grouping) drops stale items.
    return sorted(kept, key=lambda a: a.published_at or cutoff, reverse=True)
