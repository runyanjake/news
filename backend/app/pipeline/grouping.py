"""Group articles into topic buckets, either by rules + title similarity or via the LLM."""

import logging
from dataclasses import dataclass, field

from ..config import GroupingConfig
from ..llm import LLMClient, LLMError
from ..models import Article, Topic
from .text import Vector, add_into, cosine, dense_add, dense_cosine, jaccard, tokens

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


@dataclass
class _Profile:
    """A topic's articles plus their summed TF-IDF vectors and (if every article has one) embeddings."""

    articles: list[Article]
    terms: Vector = field(default_factory=dict)
    dense: list[float] | None = None

    @classmethod
    def of(cls, articles: list[Article]) -> "_Profile":
        profile = cls(articles=[])
        for article in articles:
            profile.add(cls(articles=[article], terms=dict(article.vector), dense=article.embedding))
        return profile

    def add(self, other: "_Profile") -> None:
        had_articles = bool(self.articles)
        self.articles += other.articles
        add_into(self.terms, other.terms)
        if other.dense is None or (had_articles and self.dense is None):
            self.dense = None
        elif self.dense is None:
            self.dense = list(other.dense)
        else:
            dense_add(self.dense, other.dense)


def _story_match(
    new: _Profile, existing: _Profile, threshold: float, embedding_threshold: float | None
) -> str | None:
    """Why two topics are the same story, or None.

    A shared link is conclusive. Otherwise all must hold:
    - they share several distinctive TF-IDF terms, so short texts can't match on one or two words;
    - their combined vectors are similar, so one loosely related headline can't chain stories;
    - some single pair of their articles is similar on its own, so a broad topic (e.g. a live
      blog plus explainers) can't absorb an article that only shares its theme.
    Similarity is embedding cosine when both topics have embeddings, else TF-IDF cosine.
    """
    by_url = {a.url_key: a for a in existing.articles}
    for article in new.articles:
        if (other := by_url.get(article.url_key)) is not None:
            return f"shared link {article.url_key} ({article.source_name}, {other.source_name})"
    shared = new.terms.keys() & existing.terms.keys()
    if len(shared) < MIN_SHARED_TERMS:
        return None
    if embedding_threshold is not None and new.dense is not None and existing.dense is not None:
        kind, limit, similarity = "embedding", embedding_threshold, dense_cosine(new.dense, existing.dense)
        pair_cosine = lambda x, y: dense_cosine(x.embedding, y.embedding)  # noqa: E731
    else:
        kind, limit, similarity = "tf-idf", threshold, cosine(new.terms, existing.terms)
        pair_cosine = lambda x, y: cosine(x.vector, y.vector)  # noqa: E731
    if similarity < limit:
        return None
    pair_score, a, b = max(
        ((pair_cosine(x, y), x, y) for x in new.articles for y in existing.articles), key=lambda p: p[0]
    )
    if pair_score < limit:
        return None
    top = sorted(shared, key=lambda w: new.terms[w] * existing.terms[w], reverse=True)[:5]
    return (
        f"similar coverage ({kind} cosine {similarity:.2f}; shared terms: {', '.join(top)}); "
        f'closest pair {pair_score:.2f}: "{a.title}" ({a.source_name}) ~ "{b.title}" ({b.source_name})'
    )


def story_match(
    new: list[Article], existing: list[Article], threshold: float, embedding_threshold: float | None = None
) -> str | None:
    """Public form of the merge decision for two groups of articles (used by tests/merge_eval.py)."""
    return _story_match(_Profile.of(new), _Profile.of(existing), threshold, embedding_threshold)


def merge_stories(
    topics: list[Topic], threshold: float, embedding_threshold: float | None = None
) -> list[Topic]:
    """Fold together topics that cover the same story so it yields a single report entry.

    Grouping can split a story across buckets (a keyword rule claims one outlet's
    headline while another outlet's lands in a similarity cluster, greedy clustering
    order, or the LLM). See _story_match for when two topics count as one story; the
    earliest topic keeps its name. Expects preprocessed articles (annotated, weighted,
    one copy per URL; embedded if embeddings are configured).
    """
    merged: list[Topic] = []
    profiles: list[_Profile] = []
    for topic in topics:
        profile = _Profile.of(topic.articles)
        hits = [
            (i, reason)
            for i, existing in enumerate(profiles)
            if (reason := _story_match(profile, existing, threshold, embedding_threshold))
        ]
        if not hits:
            merged.append(Topic(name=topic.name, articles=profile.articles))
            profiles.append(profile)
            continue
        # The new topic may bridge several existing ones; collapse them all into the first.
        (first, reason), *rest = hits
        target = merged[first]
        for i, bridge_reason in reversed(rest):
            other = merged.pop(i)
            profiles[first].add(profiles.pop(i))
            log.info('Merge: "%s" into "%s" via "%s": %s', other.name, target.name, topic.name, bridge_reason)
        log.info('Merge: "%s" into "%s": %s', topic.name, target.name, reason)
        profiles[first].add(profile)
        target.articles = profiles[first].articles
    embedded = embedding_threshold is not None and all(p.dense is not None for p in profiles)
    log.info("Merge (%s): %d topics -> %d stories", "embeddings" if embedded else "tf-idf", len(topics), len(merged))
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
    embedding_threshold = config.embeddings.merge_threshold if config.embeddings else None
    return merge_stories(topics, config.merge_threshold, embedding_threshold)
