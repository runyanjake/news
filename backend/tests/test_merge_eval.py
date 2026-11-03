"""Regression gate on the labeled merge fixtures (see tests/merge_eval.py).

BASELINE is what the configured thresholds achieve today; raise it when a change does better,
and never lower it without saying why in the commit.
"""

import pytest

from app.config import load_config

from .merge_eval import DATA, REPO_CONFIG, attach_embeddings, evaluate, load

GROUPING = load_config(REPO_CONFIG).grouping
FIXTURES = sorted(DATA.glob("*.json"))
# fixture -> (minimum "same" pairs merged, maximum "different" pairs merged)
BASELINE = {"2026-11-03.json": (20, 1)}


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_tfidf_merge_meets_baseline(fixture):
    _, pairs = load(fixture)
    score = evaluate(pairs, GROUPING.merge_threshold)
    min_same, max_false = BASELINE[fixture.name]
    assert score.merged["same"] >= min_same, [f"missed: {p.a.title} ~ {p.b.title}" for p in score.misses]
    assert score.false_merges <= max_false, [f"false merge: {p.a.title} ~ {p.b.title}" for p in score.misses]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_configured_embeddings_do_no_worse_than_tfidf(fixture):
    if GROUPING.embeddings is None:
        pytest.skip("embeddings not configured in config/sources.yaml")
    articles, pairs = load(fixture)
    if not attach_embeddings(articles, GROUPING.embeddings.model, fetch=False):
        pytest.skip(f"no cached embeddings for {GROUPING.embeddings.model}; run tests.merge_eval eval --embeddings")
    tfidf = evaluate(pairs, GROUPING.merge_threshold)
    embedded = evaluate(pairs, GROUPING.merge_threshold, GROUPING.embeddings.merge_threshold)
    assert embedded.merged["same"] >= tfidf.merged["same"]
    assert embedded.false_merges <= tfidf.false_merges


def test_every_fixture_has_a_baseline():
    assert {p.name for p in FIXTURES} == set(BASELINE)
