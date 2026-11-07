import asyncio

from app.config import AppConfig, GroupingConfig, PipelineConfig, ReportConfig, ScoringConfig, TopicRule
from app.models import Topic
from app.pipeline import generate_report
from app.pipeline.grouping import group_articles, group_by_keywords
from app.pipeline.preprocess import preprocess
from app.pipeline.selection import select_topics
from app.pipeline.scoring import score_topics

from .conftest import NOW, FailingConnector, FakeLLM, StaticConnector, article


def test_keyword_rules_claim_articles_before_similarity():
    config = GroupingConfig(topics=[TopicRule(name="Economy", keywords=["inflation"])])
    topics = group_by_keywords(
        [
            article("Inflation cools in September", source="a"),
            article("Fed watches inflation data", source="b"),
            article("Mars rover finds ancient lake bed", source="a"),
            article("Rover finds ancient lake bed on Mars", source="b"),
            article("Local team wins title", source="c"),
        ],
        config,
    )
    by_name = {t.name: len(t.articles) for t in topics}
    assert by_name["Economy"] == 2
    assert sorted(by_name.values()) == [1, 2, 2]


def test_multi_source_story_outranks_single_source():
    topics = group_by_keywords(
        [
            article("Senate passes budget bill", source="a", reputation=0.6),
            article("Budget bill passes Senate vote", source="b", reputation=0.6),
            article("Celebrity opens bakery", source="c", reputation=0.9),
        ],
        GroupingConfig(),
    )
    ranked = score_topics(topics, ScoringConfig(), NOW)
    assert len(ranked[0].articles) == 2
    assert ranked[0].score > ranked[1].score


def test_recency_and_reputation_order_articles():
    topics = group_by_keywords(
        [
            article("Quake strikes region", source="old", reputation=0.9, hours_old=30),
            article("Quake strikes region today", source="fresh", reputation=0.9, hours_old=1),
        ],
        GroupingConfig(),
    )
    ranked = score_topics(topics, ScoringConfig(), NOW)
    assert [a.source_id for a in ranked[0].articles] == ["fresh", "old"]


def test_llm_grouping_falls_back_on_error():
    articles = [article("One story", source="a"), article("Other thing", source="b")]
    topics = asyncio.run(group_articles(articles, GroupingConfig(method="llm"), FakeLLM(fail=True)))
    assert len(topics) == 2


def test_llm_grouping_uses_indices_and_keeps_leftovers():
    articles = [article("A1"), article("A2"), article("B1")]
    llm = FakeLLM({"topics": [{"name": "Story A", "items": [0, 1, 1, 99]}]})
    topics = asyncio.run(group_articles(articles, GroupingConfig(method="llm"), llm))
    assert [(t.name, len(t.articles)) for t in topics] == [("Story A", 2), ("B1", 1)]


def _config(**report):
    return AppConfig.model_validate(
        {"sources": [], "report": {"max_entries": 2, "sources_per_entry": 2, **report}}
    )


def test_generate_report_end_to_end(fake_llm):
    connectors = [
        StaticConnector("a", [article("Senate passes budget bill", source="a"),
                              article("Old news item", source="a", hours_old=100)]),
        StaticConnector("b", [article("Budget bill passes Senate", source="b")]),
        StaticConnector("c", [article("Budget bill clears Senate", source="c"),
                              article("Cat elected mayor", source="c")]),
        FailingConnector(),
    ]
    report = asyncio.run(generate_report(_config(), fake_llm, "2026-10-22", connectors, NOW))

    assert report.date == "2026-10-22"
    assert len(report.entries) == 2
    top = report.entries[0]
    assert (top.tagline, top.summary, top.ai_generated) == ("AI tagline", "AI summary.", True)
    assert len(top.sources) == 2  # capped at sources_per_entry
    assert "Old news item" not in str(fake_llm.calls)  # filtered by max_age_hours


def test_report_falls_back_to_top_source_without_llm():
    connectors = [StaticConnector("a", [article("Lone headline", source="a", summary="Body.")])]
    report = asyncio.run(generate_report(_config(), FakeLLM(fail=True), "2026-10-22", connectors, NOW))
    entry = report.entries[0]
    assert (entry.tagline, entry.summary, entry.ai_generated) == ("Lone headline", "Body.", False)


def test_min_sources_filters_thin_topics(fake_llm):
    connectors = [StaticConnector("a", [article("Lone headline", source="a")])]
    report = asyncio.run(
        generate_report(_config(min_sources_per_entry=2), fake_llm, "2026-10-22", connectors, NOW)
    )
    assert report.entries == []


def test_select_topics_caps_entries_per_section():
    world = [Topic(name=f"W{i}", articles=[article(f"W{i}", tags=["world", "business"])], score=10 - i)
             for i in range(3)]
    tech = Topic(name="T", articles=[article("T", tags=["technology"])], score=1)
    untagged = [Topic(name=f"U{i}", articles=[article(f"U{i}")], score=0) for i in range(2)]
    config = ReportConfig(max_entries=5, max_entries_per_tag=2)

    selected = select_topics([*world, tech, *untagged], config)

    assert [topic.name for topic, _ in selected] == ["W0", "W1", "T", "U0", "U1"]


def test_same_article_from_two_feeds_counts_once(fake_llm):
    connectors = [
        StaticConnector("a", [article("Story", source="a", url="https://x.example/s?utm_medium=rss")]),
        StaticConnector("b", [article("Story", source="b", url="http://www.x.example/s/#top")]),
    ]
    report = asyncio.run(generate_report(_config(), fake_llm, "2026-10-22", connectors, NOW))
    assert len(report.entries) == 1 and len(report.entries[0].sources) == 1


def test_preprocess_keeps_most_reputable_copy_of_an_article():
    low = article("Story", source="low", reputation=0.3, url="https://x.example/s")
    high = article("Story", source="high", reputation=0.9, url="https://x.example/s?utm_source=rss")
    other = article("Other", source="low", url="https://x.example/other")
    kept = preprocess([[low, other], [high]], PipelineConfig(), NOW)
    assert sorted(a.source_id + ":" + a.url_key for a in kept) == [
        "high:x.example/s",
        "low:x.example/other",
    ]


def test_preprocess_drops_stale_and_caps_per_source():
    feed = [article(f"Item {i}", source="a") for i in range(5)] + [article("Old", source="a", hours_old=100)]
    kept = preprocess([feed], PipelineConfig(max_items_per_source=3), NOW)
    assert [a.title for a in kept] == ["Item 0", "Item 1", "Item 2"]
