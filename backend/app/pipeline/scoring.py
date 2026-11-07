"""Score articles (reputation / recency / relevance), then score each story from its sources.

A story's score is a weighted mix of signals in 0..1, so one cutoff means the same thing every day:
- coverage: how many outlets carry it, weighted by their reputation, with diminishing returns;
- lead: the score of its best article (reputable, fresh, central to the story);
- breadth: how many sections' outlets (world, business, ...) cover it.
"""

from datetime import datetime

from ..config import ScoringConfig, StoryScoringConfig
from ..models import Article, Topic
from .text import jaccard

BREADTH_SECTIONS = 3  # sections covering a story for full breadth credit


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
    return sum(jaccard(article.words, p.words) for p in peers) / len(peers)


def best_per_source(topic: Topic) -> list[Article]:
    """Highest-scoring article from each distinct source, best first."""
    best: dict[str, Article] = {}
    for article in topic.articles:
        current = best.get(article.source_id)
        if current is None or article.score > current.score:
            best[article.source_id] = article
    return sorted(best.values(), key=lambda a: a.score, reverse=True)


def section_of(article: Article) -> str | None:
    """An article's section is its source's first tag."""
    return article.tags[0] if article.tags else None


def story_signals(sources: list[Article], config: StoryScoringConfig) -> dict[str, float]:
    """The story's signals in 0..1, from the best article of each source (best first)."""
    if not sources:
        return {"coverage": 0.0, "lead": 0.0, "breadth": 0.0}
    weighted_sources = sum(a.reputation for a in sources)
    sections = {s for a in sources if (s := section_of(a))}
    return {
        "coverage": 1 - 0.5 ** (weighted_sources / config.coverage_half),
        "lead": sources[0].score,
        "breadth": min(max(len(sections) - 1, 0) / (BREADTH_SECTIONS - 1), 1.0),
    }


def score_topics(topics: list[Topic], config: ScoringConfig, now: datetime) -> list[Topic]:
    """Mutates scores in place and returns topics ranked best first."""
    weights = config.weights
    story_weights = config.story.weights
    for topic in topics:
        for article in topic.articles:
            article.score = round(
                weights.get("reputation", 0) * article.reputation
                + weights.get("recency", 0) * recency(article, now, config.recency_half_life_hours)
                + weights.get("relevance", 0) * relevance(article, topic),
                4,
            )
        topic.articles.sort(key=lambda a: a.score, reverse=True)
        signals = story_signals(best_per_source(topic), config.story)
        topic.score = round(sum(story_weights.get(name, 0) * value for name, value in signals.items()), 4)
    return sorted(topics, key=lambda t: t.score, reverse=True)
