"""Group articles into stories: one story per event, however many outlets cover it.

Ways articles end up in one story:
- Keyword rules (grouping.topics): articles whose headlines match a rule start out as one
  cluster, which clustering can still grow. For recurring stories the vectors keep splitting.
- Embedding clustering (grouping.embeddings): each article's title + RSS description is
  embedded once, and stories are clusters of those vectors. Catches reworded coverage
  ("Netflix's new AI Discover tab" ~ "Netflix introduces AI-powered discovery").
- TF-IDF clustering (fallback): the same algorithm on word vectors, used when embeddings are
  off or the embedding server fails. Only matches stories that share distinctive words.

Both clusterings are average-linkage agglomerative: every article starts alone, and the two
clusters with the highest average pairwise cosine merge until none reach the threshold. The
number of stories is not fixed in advance, and averaging over all pairs stops one loosely
related article from chaining two stories together. A merge is vetoed when the clusters share
fewer than MIN_SHARED_TERMS distinctive words. Same-link copies were already folded into one
article by preprocess.
"""

import logging
import time
from dataclasses import dataclass

import numpy as np

from .. import metrics
from ..config import GroupingConfig
from ..models import Article, Topic
from .text import tokens

log = logging.getLogger(__name__)

MIN_SHARED_TERMS = 3  # short texts can look alike on one or two words ("need", "rule")
NEAR_MISS_SHARE = 0.85  # log unmerged pairs scoring at least this share of the threshold
MAX_NEAR_MISSES = 10


@dataclass
class Clustering:
    clusters: list[list[int]]  # article indices, each cluster in index order
    joins: list[tuple[list[int], list[int], float]]  # (cluster, cluster, average cosine) per merge
    vetoes: list[tuple[list[int], list[int], float, int]]  # (..., shared terms) per vetoed merge
    near_misses: list[tuple[list[int], list[int], float]]  # best unmerged pairs below the threshold


def similarity_matrix(articles: list[Article], use_embeddings: bool) -> np.ndarray:
    """Pairwise cosine of the articles' unit vectors (embeddings or TF-IDF)."""
    if use_embeddings:
        vectors = np.array([a.embedding for a in articles], dtype=float)
    else:
        vocabulary = {w: i for i, w in enumerate(sorted({w for a in articles for w in a.vector}))}
        vectors = np.zeros((len(articles), len(vocabulary)))
        for row, article in enumerate(articles):
            for word, weight in article.vector.items():
                vectors[row, vocabulary[word]] = weight
    return vectors @ vectors.T


def average_linkage(
    sim: np.ndarray, terms: list[set[str]], threshold: float, groups: list[list[int]] | None = None
) -> Clustering:
    """Clusters of article indices; `groups` (a partition of the articles) seeds the starting clusters."""
    members = [sorted(g) for g in groups] if groups is not None else [[i] for i in range(len(sim))]
    n = len(members)
    membership = np.zeros((n, len(sim)))
    for row, group in enumerate(members):
        membership[row, group] = 1 / len(group)
    avg = membership @ sim @ membership.T  # average cosine between clusters (Lance-Williams update)
    blocked = np.eye(n, dtype=bool)  # self-pairs, merged-away clusters and vetoed pairs
    alive = np.ones(n, dtype=bool)
    terms = [set().union(*(terms[i] for i in group)) for group in members]
    joins, vetoes = [], []
    while True:
        candidates = np.where(blocked, -np.inf, avg)
        i, j = np.unravel_index(np.argmax(candidates), candidates.shape)
        score = float(candidates[i, j])
        if score < threshold:
            break
        i, j = min(i, j), max(i, j)
        shared = len(terms[i] & terms[j])
        if shared < MIN_SHARED_TERMS:
            vetoes.append((members[i], members[j], score, shared))
            blocked[i, j] = blocked[j, i] = True
            continue
        joins.append((members[i], members[j], score))
        size_i, size_j = len(members[i]), len(members[j])
        avg[i, :] = avg[:, i] = (size_i * avg[i, :] + size_j * avg[j, :]) / (size_i + size_j)
        members[i] = sorted(members[i] + members[j])
        members[j] = []
        terms[i] |= terms[j]
        alive[j] = False
        blocked[i, :] = blocked[:, i] = ~alive  # a bigger cluster may now share enough terms
        blocked[i, i] = True
        blocked[j, :] = blocked[:, j] = True
    active = [k for k, m in enumerate(members) if m]
    floor = threshold * NEAR_MISS_SHARE
    near = sorted(
        ((float(avg[a, b]), a, b) for x, a in enumerate(active) for b in active[x + 1 :]
         if floor <= avg[a, b] < threshold),
        reverse=True,
    )[:MAX_NEAR_MISSES]
    return Clustering(
        clusters=sorted((members[k] for k in active), key=lambda m: m[0]),
        joins=joins,
        vetoes=vetoes,
        near_misses=[(members[a], members[b], score) for score, a, b in near],
    )


def central_order(cluster: list[int], sim: np.ndarray) -> list[int]:
    """The cluster's articles, most central first (highest average cosine to the others)."""
    if len(cluster) == 1:
        return list(cluster)
    block = sim[np.ix_(cluster, cluster)]
    centrality = (block.sum(axis=1) - block.diagonal()) / (len(cluster) - 1)
    return [cluster[k] for k in np.argsort(-centrality, kind="stable")]


def cohesion(cluster: list[int], sim: np.ndarray) -> float:
    """Average pairwise cosine within a cluster of two or more articles."""
    block = sim[np.ix_(cluster, cluster)]
    return float((block.sum() - block.trace()) / (len(cluster) * (len(cluster) - 1)))


def _label(articles: list[Article], cluster: list[int]) -> str:
    first = articles[cluster[0]].title
    return f'"{first}"' + (f" (+{len(cluster) - 1})" if len(cluster) > 1 else "")


def _rule_groups(articles: list[Article], config: GroupingConfig) -> tuple[list[list[int]], dict[int, str]]:
    """Starting clusters: one per keyword rule that matched, one per other article; and rule names."""
    # Normalise keywords like headlines, so a "strikes" rule matches "strike".
    rules = [(rule.name, tokens(" ".join(rule.keywords))) for rule in config.topics]
    pinned: dict[str, list[int]] = {}
    groups = []
    for i, article in enumerate(articles):
        name = next((name for name, keys in rules if article.words & keys), None)
        if name:
            pinned.setdefault(name, []).append(i)
        else:
            groups.append([i])
    names = {group[0]: name for name, group in pinned.items()}
    return list(pinned.values()) + groups, names


def group_articles(articles: list[Article], config: GroupingConfig) -> list[Topic]:
    """Stories from preprocessed articles (embedded when embeddings are configured and worked)."""
    if not articles:
        return []
    started = time.monotonic()
    use_embeddings = config.embeddings is not None and all(a.embedding is not None for a in articles)
    kind = "embedding" if use_embeddings else "tf-idf"
    threshold = config.embeddings.threshold if use_embeddings else config.tfidf_threshold
    sim = similarity_matrix(articles, use_embeddings)
    groups, rule_names = _rule_groups(articles, config)
    result = average_linkage(sim, [set(a.vector) for a in articles], threshold, groups)

    for left, right, score in result.joins:
        log.info("Cluster: joined %s + %s: average cosine %.2f", _label(articles, left), _label(articles, right), score)
    for left, right, score, shared in result.vetoes:
        log.info("Cluster: vetoed %s + %s: average cosine %.2f but %d shared term(s)",
                 _label(articles, left), _label(articles, right), score, shared)
    for left, right, score in result.near_misses:
        log.debug("Cluster: near miss %.2f < %.2f: %s ~ %s", score, threshold,
                  _label(articles, left), _label(articles, right))

    topics = []
    for cluster in result.clusters:
        ordered = [articles[k] for k in central_order(cluster, sim)]
        name = next((rule_names[k] for k in cluster if k in rule_names), ordered[0].title)
        topics.append(Topic(name=name, articles=ordered, central=ordered[: config.central_articles]))
        if len(cluster) > 1:
            score = cohesion(cluster, sim)
            metrics.CLUSTER_COHESION.labels(kind).observe(score)
            log.info('Story "%s" (%d articles, cohesion %.2f): %s', name, len(ordered), score,
                     "; ".join(f'[{a.source_name}] "{a.title}"' for a in ordered))

    multi = sum(1 for c in result.clusters if len(c) > 1)
    largest = max(len(c) for c in result.clusters)
    metrics.CLUSTER_RUNS.labels(kind).inc()
    metrics.CLUSTER_DURATION.labels(kind).observe(time.monotonic() - started)
    metrics.CLUSTER_STORIES.labels("multi").set(multi)
    metrics.CLUSTER_STORIES.labels("single").set(len(result.clusters) - multi)
    metrics.CLUSTER_VETOES.set(len(result.vetoes))
    log.info(
        "Cluster (%s, average linkage, threshold %.2f): %d articles -> %d stories "
        "(%d with more than one article, largest %d; %d keyword rules matched; %d vetoed merges) in %.2fs",
        kind, threshold, len(articles), len(result.clusters), multi, largest, len(rule_names),
        len(result.vetoes), time.monotonic() - started,
    )
    return topics
