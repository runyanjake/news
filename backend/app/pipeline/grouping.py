"""Group articles into topic buckets, either by rules + title similarity or via the LLM."""

import logging

from ..config import GroupingConfig
from ..llm import LLMClient, LLMError
from ..models import Article, Topic
from .text import Vector, add_into, cosine, jaccard, tokens

log = logging.getLogger(__name__)

MAX_LLM_ITEMS = 200
MIN_SHARED_TERMS = 3  # short texts can look alike on one or two words ("need", "rule")


def group_by_keywords(articles: list[Article], config: GroupingConfig) -> list[Topic]:
    """Manual rules claim matching articles first; the rest cluster by title similarity."""
    # Normalise keywords like headlines, so a "strikes" rule matches "strike".
    rules = [(rule.name, tokens(" ".join(rule.keywords))) for rule in config.topics]
    pinned: dict[str, list[Article]] = {name: [] for name, _ in rules}
    clusters: list[tuple[list[frozenset[str]], list[Article]]] = []

    for article in sorted(articles, key=lambda a: a.reputation, reverse=True):
        words = article.words
        rule_name = next((name for name, keys in rules if words & keys), None)
        if rule_name:
            pinned[rule_name].append(article)
            continue
        best, best_sim = None, config.similarity_threshold
        for cluster in clusters:
            sim = max(jaccard(words, member) for member in cluster[0])
            if sim >= best_sim:
                best, best_sim = cluster, sim
        if best is None:
            clusters.append(([words], [article]))
        else:
            best[0].append(words)
            best[1].append(article)

    topics = [Topic(name=name, articles=items) for name, items in pinned.items() if items]
    topics += [Topic(name=items[0].title, articles=items) for _, items in clusters]
    return topics


async def group_by_llm(articles: list[Article], llm: LLMClient) -> list[Topic]:
    items = articles[:MAX_LLM_ITEMS]
    if len(items) < len(articles):
        log.warning("Group: LLM grouping sees only the newest %d of %d articles", len(items), len(articles))
    listing = "\n".join(f"{i}: [{a.source_name}] {a.title}" for i, a in enumerate(items))
    result = await llm.complete_json(
        system=(
            "You group news headlines that describe the same story or closely related "
            'developments. Reply with JSON only: {"topics": [{"name": "short topic name", '
            '"items": [indices]}]}. Every index appears at most once.'
        ),
        user=listing,
    )
    topics, used = [], set()
    for group in result.get("topics", []):
        members = []
        for i in group.get("items", []):
            if isinstance(i, int) and 0 <= i < len(items) and i not in used:
                used.add(i)
                members.append(items[i])
        if members:
            topics.append(Topic(name=str(group.get("name") or members[0].title), articles=members))
    topics += [Topic(name=a.title, articles=[a]) for i, a in enumerate(items) if i not in used]
    return topics


def _centroid(articles: list[Article]) -> Vector:
    total: Vector = {}
    for article in articles:
        add_into(total, article.vector)
    return total


def _story_match(
    new: list[Article], new_vec: Vector, existing: list[Article], existing_vec: Vector, threshold: float
) -> str | None:
    """Why two topics are the same story, or None.

    A shared link is conclusive. Otherwise both must hold:
    - the topics' combined TF-IDF vectors (all titles + summaries) are similar and share
      several terms, so one loosely related headline can't chain two stories together;
    - some single pair of their articles is similar on its own, so a broad topic (e.g. a
      live blog plus explainers) can't absorb an article that only shares its theme.
    """
    by_url = {a.url_key: a for a in existing}
    for article in new:
        if (other := by_url.get(article.url_key)) is not None:
            return f"shared link {article.url_key} ({article.source_name}, {other.source_name})"
    similarity = cosine(new_vec, existing_vec)
    if similarity < threshold:
        return None
    shared = sorted(new_vec.keys() & existing_vec.keys(), key=lambda w: new_vec[w] * existing_vec[w], reverse=True)
    if len(shared) < MIN_SHARED_TERMS:
        return None
    pair_score, a, b = max(
        ((cosine(x.vector, y.vector), x, y) for x in new for y in existing), key=lambda p: p[0]
    )
    if pair_score < threshold:
        return None
    return (
        f"similar coverage (cosine {similarity:.2f}; shared terms: {', '.join(shared[:5])}); "
        f'closest pair {pair_score:.2f}: "{a.title}" ({a.source_name}) ~ "{b.title}" ({b.source_name})'
    )


def merge_stories(topics: list[Topic], threshold: float) -> list[Topic]:
    """Fold together topics that cover the same story so it yields a single report entry.

    Grouping can split a story across buckets (a keyword rule claims one outlet's
    headline while another outlet's lands in a similarity cluster, greedy clustering
    order, or the LLM). Topics merge when they share an article URL or their TF-IDF
    centroids reach `threshold`; the earliest topic keeps its name. Expects
    preprocessed articles (annotated, weighted, one copy per URL).
    """
    merged: list[Topic] = []
    centroids: list[Vector] = []
    for topic in topics:
        vec = _centroid(topic.articles)
        hits = [
            (i, reason)
            for i, existing in enumerate(merged)
            if (reason := _story_match(topic.articles, vec, existing.articles, centroids[i], threshold))
        ]
        if not hits:
            merged.append(Topic(name=topic.name, articles=list(topic.articles)))
            centroids.append(vec)
            continue
        # The new topic may bridge several existing ones; collapse them all into the first.
        (first, reason), *rest = hits
        target = merged[first]
        for i, bridge_reason in reversed(rest):
            other = merged.pop(i)
            add_into(centroids[first], centroids.pop(i))
            log.info('Merge: "%s" into "%s" via "%s": %s', other.name, target.name, topic.name, bridge_reason)
            target.articles += other.articles
        log.info('Merge: "%s" into "%s": %s', topic.name, target.name, reason)
        target.articles += topic.articles
        add_into(centroids[first], vec)
    log.info("Merge: %d topics -> %d stories", len(topics), len(merged))
    return merged


async def group_articles(
    articles: list[Article], config: GroupingConfig, llm: LLMClient | None
) -> list[Topic]:
    topics, method = None, "keywords"
    if config.method == "llm" and llm is not None:
        try:
            topics, method = await group_by_llm(articles, llm), "llm"
        except LLMError as exc:
            log.warning("Group: LLM grouping failed, falling back to keywords: %s", exc)
    if topics is None:
        topics = group_by_keywords(articles, config)
    shared = sum(1 for t in topics if len(t.articles) > 1)
    log.info(
        "Group (%s): %d articles -> %d topics (%d with more than one article)",
        method, sum(len(t.articles) for t in topics), len(topics), shared,
    )
    return merge_stories(topics, config.merge_threshold)
