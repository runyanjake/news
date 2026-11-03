"""Group articles into topic buckets, either by rules + title similarity or via the LLM."""

import logging

from ..config import GroupingConfig
from ..llm import LLMClient, LLMError
from ..models import Article, Topic
from .text import jaccard

log = logging.getLogger(__name__)

MAX_LLM_ITEMS = 200


def group_by_keywords(articles: list[Article], config: GroupingConfig) -> list[Topic]:
    """Manual rules claim matching articles first; the rest cluster by title similarity."""
    rules = [(rule.name, {k.lower() for k in rule.keywords}) for rule in config.topics]
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


StoryKey = tuple[list[frozenset[str]], set[str]]  # (title words, url keys) of a topic


def _story_key(articles: list[Article]) -> StoryKey:
    return [a.words for a in articles], {a.url_key for a in articles}


def _same_story(a: StoryKey, b: StoryKey, threshold: float) -> bool:
    if a[1] & b[1]:
        return True
    return any(jaccard(x, y) >= threshold for x in a[0] for y in b[0])


def merge_stories(topics: list[Topic], threshold: float) -> list[Topic]:
    """Fold together topics that cover the same story so it yields a single report entry.

    Grouping can split a story across buckets (a keyword rule claims one outlet's
    headline while another outlet's lands in a similarity cluster, greedy clustering
    order, or the LLM). Topics merge when they share an article URL or any pair of
    their headlines meets the similarity threshold; the earliest topic keeps its name.
    Expects preprocessed articles (annotated, one copy per URL).
    """
    merged: list[Topic] = []
    keys: list[StoryKey] = []
    for topic in topics:
        key = _story_key(topic.articles)
        hits = [i for i, other in enumerate(keys) if _same_story(key, other, threshold)]
        if not hits:
            merged.append(Topic(name=topic.name, articles=list(topic.articles)))
            keys.append(key)
            continue
        # The new topic may bridge several existing ones; collapse them all into the first.
        first, *rest = hits
        target, target_key = merged[first], keys[first]
        for i in reversed(rest):
            target.articles += merged.pop(i).articles
            other = keys.pop(i)
            target_key[0].extend(other[0])
            target_key[1].update(other[1])
        target.articles += topic.articles
        target_key[0].extend(key[0])
        target_key[1].update(key[1])
    return merged


async def group_articles(
    articles: list[Article], config: GroupingConfig, llm: LLMClient | None
) -> list[Topic]:
    topics = None
    if config.method == "llm" and llm is not None:
        try:
            topics = await group_by_llm(articles, llm)
        except LLMError as exc:
            log.warning("LLM grouping failed, falling back to keywords: %s", exc)
    if topics is None:
        topics = group_by_keywords(articles, config)
    return merge_stories(topics, config.similarity_threshold)
