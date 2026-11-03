"""Story merging: one report entry per story, decided on derived keys only."""

import asyncio
import logging

from app.config import AppConfig, GroupingConfig, PipelineConfig, TopicRule
from app.models import Topic
from app.pipeline import generate_report
from app.pipeline.grouping import group_articles, merge_stories
from app.pipeline.preprocess import preprocess, weigh_terms
from app.pipeline.text import tokens

from .conftest import NOW, FakeLLM, StaticConnector, article

# Unrelated coverage, so word weights reflect a realistic day rather than a two-article corpus.
BACKGROUND = [
    ("Rover finds ancient lake bed on Mars", "The probe's instruments detected sediment layers in a crater."),
    ("Central bank holds interest rates", "Policymakers kept borrowing costs unchanged and flagged inflation risks."),
    ("Local team wins championship title", "Fans celebrated downtown after the final whistle of a tense game."),
    ("New battery chemistry doubles range", "Researchers say the cells charge faster and last longer in the cold."),
    ("Wildfire forces evacuations in hills", "Firefighters battled strong winds as thousands left their homes."),
    ("Midterm turnout surges in key states", "Voters queued for hours as the election tests the president's party."),
    ("Election officials report smooth voting", "Polling stations opened on time for the midterm election across states."),
]

STRIKE_BBC = article(
    "US strikes Iranian island ahead of midterm elections", source="bbc",
    summary="American forces hit targets on Qeshm island in the Strait of Hormuz on Tuesday.",
)
STRIKE_WSJ = article(
    "U.S. Strikes Iran's Qeshm Island as Trump Talks Down War Risks", source="wsj",
    summary="The strikes on Qeshm island near the Strait of Hormuz came as the president played down escalation.",
)
LIVE_BLOG = article(
    "US midterm elections 2026 live: turnout surges in vote that will define Trump presidency", source="guardian",
    summary="Follow live results as voters choose the House, Senate and governors in the midterm election.",
)


def weighed(*articles):
    """Weigh the articles against a background day, as preprocess does, and return them."""
    background = [article(t, source=f"bg{i}", summary=s) for i, (t, s) in enumerate(BACKGROUND)]
    weigh_terms([*articles, *background])
    return list(articles)


def topics(*articles):
    return [Topic(name=a.title, articles=[a]) for a in articles]


def test_tokens_normalise_acronyms_possessives_and_plurals():
    assert tokens("U.S. Strikes Iran’s island") == tokens("US strike Iran islands") == {"us", "strike", "iran", "island"}


def test_reworded_coverage_of_one_event_merges():
    merged = merge_stories(topics(*weighed(STRIKE_BBC, STRIKE_WSJ)), threshold=0.2)
    assert [len(t.articles) for t in merged] == [2]


def test_shared_context_words_alone_do_not_merge():
    # Both mention the midterms, but one is a military strike and the other the vote itself.
    merged = merge_stories(topics(*weighed(STRIKE_BBC, LIVE_BLOG)), threshold=0.2)
    assert [len(t.articles) for t in merged] == [1, 1]


def test_feed_boilerplate_does_not_make_items_alike():
    doi = "Nature, Published online: 03 November 2026; doi:10.1038/d41586-026-0{}"
    nature = [
        article("Research bonuses need clear and fair rules", source="nature", summary=doi.format(1)),
        article("Cities are changing and architects must adapt", source="nature", summary=doi.format(2)),
        article("Cholesterol drug flops in the clinic", source="nature", summary=doi.format(3)),
    ]
    merged = merge_stories(topics(*weighed(*nature)), threshold=0.2)
    assert len(merged) == 3


def test_needs_several_shared_terms():
    a, b = article("A", source="a"), article("B", source="b")
    a.vector, b.vector = {"rule": 0.8, "need": 0.6}, {"rule": 0.6, "need": 0.8}  # cosine 0.96, 2 terms
    assert len(merge_stories(topics(a, b), threshold=0.2)) == 2


def test_broad_topic_does_not_absorb_an_article_sharing_only_its_theme():
    # A live blog plus explainers: together they look like "midterms" (combined cosine 0.36),
    # but the strike story matches none of them individually (best pair 0.18).
    theme = {"midterm": 0.3, "election": 0.3, "vote": 0.3}
    members = [article(f"Midterm piece {i}", source=f"m{i}") for i in range(6)]
    for i, member in enumerate(members):
        member.vector = {**theme, f"detail{i}a": 1.0, f"detail{i}b": 1.0, f"detail{i}c": 1.0}
    strike = article("US strikes Iranian island ahead of midterm elections", source="bbc")
    strike.vector = {"midterm": 1.0, "election": 1.0, "vote": 1.0, "strike": 1.0, "island": 1.0, "qeshm": 1.0,
                     "iran": 1.0, "hormuz": 1.0}
    merged = merge_stories([Topic(name="Midterms", articles=members), *topics(strike)], threshold=0.2)
    assert [t.name for t in merged] == ["Midterms", strike.title]


def test_topic_bridging_two_others_collapses_all_three():
    a, b, c = (article(name, source=name) for name in "abc")
    a.vector = {"x1": 1.0, "x2": 1.0, "x3": 1.0}
    b.vector = {"y1": 1.0, "y2": 1.0, "y3": 1.0}
    c.vector = {"x1": 1.0, "x2": 1.0, "x3": 1.0, "y1": 1.0, "y2": 1.0, "y3": 1.0}
    merged = merge_stories(topics(a, b, c), threshold=0.5)
    assert [(t.name, len(t.articles)) for t in merged] == [("a", 3)]


def test_shared_link_merges_llm_groups():
    first = article("Same link, different feed", source="d", url="https://x.example/story?utm_source=rss")
    second = article("Unrelated wording entirely", source="e", url="https://www.x.example/story/")
    llm = FakeLLM({"topics": [{"name": "One", "items": [0]}, {"name": "Two", "items": [1]}]})
    merged = asyncio.run(group_articles(weighed(first, second), GroupingConfig(method="llm"), llm))
    assert [(t.name, len(t.articles)) for t in merged] == [("One", 2)]


def test_story_split_by_keyword_rule_is_merged():
    # The rule claims both "Qeshm" headlines; the merge still keeps the live blog separate.
    config = GroupingConfig(topics=[TopicRule(name="Iran strikes", keywords=["qeshm"])])
    merged = asyncio.run(group_articles(weighed(STRIKE_WSJ, STRIKE_BBC, LIVE_BLOG), config, None))
    assert sorted((t.name, len(t.articles)) for t in merged) == [("Iran strikes", 2), (LIVE_BLOG.title, 1)]


def test_merge_logs_reason_and_sources(caplog):
    with caplog.at_level(logging.INFO, logger="app.pipeline.grouping"):
        merge_stories(topics(*weighed(STRIKE_BBC, STRIKE_WSJ)), threshold=0.2)
    reason, summary = caplog.messages
    assert reason.startswith(f'Merge: "{STRIKE_WSJ.title}" into "{STRIKE_BBC.title}": similar coverage (cosine ')
    assert "qeshm" in reason
    assert reason.endswith(f'"{STRIKE_WSJ.title}" (WSJ) ~ "{STRIKE_BBC.title}" (BBC)')
    assert summary == "Merge: 2 topics -> 1 stories"


def test_comparison_keys_never_change_what_the_report_shows():
    raw = STRIKE_WSJ.model_copy()
    (kept,) = preprocess([[raw]], PipelineConfig(), NOW)
    assert (kept.title, kept.summary, kept.url) == (STRIKE_WSJ.title, STRIKE_WSJ.summary, STRIKE_WSJ.url)
    assert "url_key" not in kept.model_dump() and "vector" not in kept.model_dump()


def test_report_has_one_entry_per_story_with_original_headlines():
    background = [article(t, source=f"bg{i}", summary=s) for i, (t, s) in enumerate(BACKGROUND)]
    connectors = [
        StaticConnector("bbc", [STRIKE_BBC.model_copy()]),
        StaticConnector("wsj", [STRIKE_WSJ.model_copy()]),
        StaticConnector("bg", background),
    ]
    config = AppConfig.model_validate({"sources": [], "report": {"max_entries": 20}})
    report = asyncio.run(generate_report(config, FakeLLM(fail=True), "2026-11-03", connectors, NOW))
    strike = [e for e in report.entries if "Qeshm" in " ".join(s.title for s in e.sources)]
    assert len(strike) == 1
    assert {s.title for s in strike[0].sources} == {STRIKE_BBC.title, STRIKE_WSJ.title}
    assert strike[0].tagline in {STRIKE_BBC.title, STRIKE_WSJ.title}  # LLM down: the top source's own headline
