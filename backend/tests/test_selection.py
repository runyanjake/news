import logging

import pytest
from pydantic import ValidationError

from app.config import ReportConfig, ScoringConfig, StoryScoringConfig
from app.models import Topic
from app.pipeline.scoring import score_topics, story_signals
from app.pipeline.selection import cutoff, select_topics, story_section

from .conftest import NOW, article


def _story(name, score, tags=("world",), sources=1):
    articles = [article(f"{name} {i}", source=f"{name}-{i}", tags=tags) for i in range(sources)]
    return Topic(name=name, articles=articles, score=score)


def _names(selected):
    return [topic.name for topic, _ in selected]


def test_coverage_has_diminishing_returns():
    config = StoryScoringConfig()
    coverage = [
        story_signals([article("x", source=str(i), reputation=0.8) for i in range(n)], config)["coverage"]
        for n in range(1, 6)
    ]
    gains = [b - a for a, b in zip(coverage, coverage[1:])]
    assert all(0 < c < 1 for c in coverage)
    assert gains == sorted(gains, reverse=True)


def test_breadth_counts_sections_covering_a_story():
    config = StoryScoringConfig()
    one = [article("x", source="a", tags=["world"]), article("x", source="b", tags=["world"])]
    two = [article("x", source="a", tags=["world"]), article("x", source="b", tags=["business"])]
    assert story_signals(one, config)["breadth"] == 0
    assert story_signals(two, config)["breadth"] == 0.5


def test_story_score_is_a_weighted_mix_in_unit_range():
    topic = Topic(name="t", articles=[article("Budget passes", source=s, reputation=0.9) for s in "abcdef"])
    [ranked] = score_topics([topic], ScoringConfig(), NOW)
    assert 0 < ranked.score <= 1
    weights = {"coverage": 0, "lead": 1, "breadth": 0}
    [lead_only] = score_topics([topic], ScoringConfig(story=StoryScoringConfig(weights=weights)), NOW)
    assert lead_only.score == lead_only.articles[0].score


def test_story_section_is_the_most_common_source_section():
    top = [article("x", source="a", tags=["world"]), article("x", source="b", tags=["technology"]),
           article("x", source="c", tags=["technology", "business"])]
    assert story_section(top) == "technology"
    assert story_section(top[:2]) == "world"  # a tie goes to the better source
    assert story_section([article("x")]) is None


def test_cutoff_rises_from_start_to_end_past_min_entries():
    config = ReportConfig(min_entries=10, max_entries=20, cutoff_start=0.5, cutoff_end=0.6)
    assert cutoff(10, config) is None
    assert cutoff(11, config) == pytest.approx(0.5)
    assert cutoff(20, config) == pytest.approx(0.6)
    assert cutoff(15, config) == pytest.approx(0.5 + 0.1 * 4 / 9)


def test_min_entries_defaults_to_max_and_must_not_exceed_it():
    assert ReportConfig(max_entries=7).min_entries == 7
    with pytest.raises(ValidationError):
        ReportConfig(min_entries=8, max_entries=7)


def test_takes_min_entries_then_stops_at_the_cutoff():
    ranked = [_story(f"S{i}", score) for i, score in enumerate([0.9, 0.8, 0.3, 0.7, 0.2])]
    # min 2, max 5: story 3 needs 0.5 and S2 (0.3) falls short, so S3 isn't reached either.
    config = ReportConfig(min_entries=2, max_entries=5, cutoff_start=0.5, cutoff_end=0.8)
    assert _names(select_topics(ranked, config)) == ["S0", "S1"]


def test_busy_day_fills_to_max_entries():
    ranked = [_story(f"S{i}", 0.9) for i in range(30)]
    config = ReportConfig(min_entries=10, max_entries=20)
    assert len(select_topics(ranked, config)) == 20


def test_quiet_day_still_gets_min_entries():
    ranked = [_story(f"S{i}", 0.1) for i in range(30)]
    config = ReportConfig(min_entries=10, max_entries=20)
    assert len(select_topics(ranked, config)) == 10


def test_every_section_gets_its_best_story_whatever_its_score():
    world = [_story(f"W{i}", 0.9 - i / 100) for i in range(5)]
    science = [_story("Sci0", 0.1, tags=["science"]), _story("Sci1", 0.05, tags=["science"])]
    config = ReportConfig(min_entries=3, max_entries=6, cutoff_start=0.5, cutoff_end=0.5)

    selected = select_topics([*world, *science], config)

    assert _names(selected) == ["W0", "W1", "W2", "W3", "W4", "Sci0"]  # rank order
    off = select_topics([*world, *science], config.model_copy(update={"cover_sections": False}))
    assert "Sci0" not in _names(off)


def test_select_logs_why_it_stopped(caplog):
    ranked = [_story("A", 0.9), _story("B", 0.2, tags=["science"]), _story("C", 0.3)]
    config = ReportConfig(min_entries=1, max_entries=3, cutoff_start=0.5, cutoff_end=0.5)
    with caplog.at_level(logging.INFO, logger="app.pipeline.selection"):
        select_topics(ranked, config)
    assert caplog.messages == [
        "Select: 2 of 3 stories (2 section leads; stopped: story 3 scored 0.30, below the cutoff of 0.50)"
    ]
