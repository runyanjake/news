"""Score articles (reputation / recency / relevance) and rank topics by them."""

from datetime import datetime

from ..config import ScoringConfig
from ..models import Article, Topic
from .text import jaccard, tokens


def recency(article: Article, now: datetime, half_life_hours: float) -> float:
    if article.published_at is None:
        return 0.5
    age_hours = max((now - article.published_at).total_seconds() / 3600, 0.0)
    return 0.5 ** (age_hours / half_life_hours)


def relevance(article: Article, topic: Topic) -> float:
    """How central the article is to its topic: mean title similarity to its peers."""
    peers = [a for a in topic.articles if a is not article]
    if not peers:
        return 0.0
    words = tokens(article.title)
    return sum(jaccard(words, tokens(p.title)) for p in peers) / len(peers)


def best_per_source(topic: Topic) -> list[Article]:
    """Highest-scoring article from each distinct source, best first."""
    best: dict[str, Article] = {}
    for article in topic.articles:
        current = best.get(article.source_id)
        if current is None or article.score > current.score:
            best[article.source_id] = article
    return sorted(best.values(), key=lambda a: a.score, reverse=True)


def score_topics(
    topics: list[Topic], config: ScoringConfig, now: datetime, sources_per_entry: int
) -> list[Topic]:
    """Mutates scores in place and returns topics ranked best first.

    A topic's score sums its best article per source (capped at N sources), so a
    story carried by several reputable outlets outranks a single-source item.
    """
    w = config.weights
    for topic in topics:
        for article in topic.articles:
            article.score = round(
                w.get("reputation", 0) * article.reputation
                + w.get("recency", 0) * recency(article, now, config.recency_half_life_hours)
                + w.get("relevance", 0) * relevance(article, topic),
                4,
            )
        topic.articles.sort(key=lambda a: a.score, reverse=True)
        topic.score = round(sum(a.score for a in best_per_source(topic)[:sources_per_entry]), 4)
    return sorted(topics, key=lambda t: t.score, reverse=True)
