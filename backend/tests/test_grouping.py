"""Story clustering: one report entry per story, decided on derived keys only."""

import asyncio
import logging

import numpy as np

from app import metrics
from app.config import AppConfig, EmbeddingConfig, GroupingConfig, PipelineConfig, TopicRule
from app.models import Topic
from app.pipeline import generate_report
from app.pipeline.grouping import average_linkage, central_order, group_articles
from app.pipeline.summarize import summarize_topic
from app.pipeline.preprocess import embed_articles, preprocess, weigh_terms
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


def sizes(*articles, **config):
    """Story sizes from clustering the articles, in the order stories come out."""
    return [len(t.articles) for t in group_articles(list(articles), GroupingConfig(**config))]


def test_tokens_normalise_acronyms_possessives_and_plurals():
    assert tokens("U.S. Strikes Iran’s island") == tokens("US strike Iran islands") == {"us", "strike", "iran", "island"}


def test_reworded_coverage_of_one_event_clusters():
    assert sizes(*weighed(STRIKE_BBC, STRIKE_WSJ)) == [2]


def test_shared_context_words_alone_do_not_cluster():
    # Both mention the midterms, but one is a military strike and the other the vote itself.
    assert sizes(*weighed(STRIKE_BBC, LIVE_BLOG)) == [1, 1]


def test_feed_boilerplate_does_not_make_items_alike():
    doi = "Nature, Published online: 03 November 2026; doi:10.1038/d41586-026-0{}"
    nature = [
        article("Research bonuses need clear and fair rules", source="nature", summary=doi.format(1)),
        article("Cities are changing and architects must adapt", source="nature", summary=doi.format(2)),
        article("Cholesterol drug flops in the clinic", source="nature", summary=doi.format(3)),
    ]
    assert sizes(*weighed(*nature)) == [1, 1, 1]


def test_needs_several_shared_terms():
    a, b = article("A", source="a"), article("B", source="b")
    a.vector, b.vector = {"rule": 0.8, "need": 0.6}, {"rule": 0.6, "need": 0.8}  # cosine 0.96, 2 terms
    assert sizes(a, b) == [1, 1]


def test_average_linkage_does_not_chain_through_one_bridging_article():
    # a~b and b~c are close, a~c is not: single linkage would chain all three.
    sim = np.array([[1.0, 0.9, 0.1], [0.9, 1.0, 0.5], [0.1, 0.5, 1.0]])
    terms = [{"x", "y", "z"}] * 3
    assert average_linkage(sim, terms, 0.6).clusters == [[0, 1], [2]]
    assert average_linkage(sim, terms, 0.25).clusters == [[0, 1, 2]]  # (0.1 + 0.5) / 2 = 0.3


def test_vetoed_pair_can_merge_once_its_cluster_shares_enough_terms():
    sim = np.array([[1.0, 0.9, 0.85], [0.9, 1.0, 0.85], [0.85, 0.85, 1.0]])
    terms = [{"a", "b", "e"}, {"c", "f", "g"}, {"a", "b", "c", "e", "f", "g"}]
    result = average_linkage(sim, terms, 0.8)
    assert result.clusters == [[0, 1, 2]]
    assert [(left, right) for left, right, _, _ in result.vetoes] == [([0], [1])]


def test_keyword_rules_seed_a_story_that_clustering_can_grow():
    # The rule claims only the WSJ headline (the BBC one doesn't say "Qeshm"); similarity adds BBC.
    config = GroupingConfig(topics=[TopicRule(name="Iran strikes", keywords=["qeshm"])])
    stories = group_articles(weighed(STRIKE_WSJ, STRIKE_BBC, LIVE_BLOG), config)
    assert sorted((t.name, len(t.articles)) for t in stories) == [("Iran strikes", 2), (LIVE_BLOG.title, 1)]


def test_stories_are_named_after_and_written_from_their_most_central_articles():
    sim = np.array([[1.0, 0.2, 0.3], [0.2, 1.0, 0.9], [0.3, 0.9, 1.0]])
    assert central_order([0, 1, 2], sim) == [2, 1, 0]
    (story,) = group_articles(weighed(STRIKE_BBC, STRIKE_WSJ), GroupingConfig(central_articles=1))
    assert story.name == story.articles[0].title and story.central == story.articles[:1]


def test_llm_writes_from_central_articles_but_entry_lists_top_sources(fake_llm):
    top = [article("Top scored", source="a"), article("Second", source="b")]
    topic = Topic(name="Story", articles=top, central=[top[1]])
    entry = asyncio.run(summarize_topic(topic, top, fake_llm))
    assert "Second" in fake_llm.calls[0] and "Top scored" not in fake_llm.calls[0]
    assert [s.title for s in entry.sources] == ["Top scored", "Second"]


def test_cluster_logs_joins_stories_and_summary(caplog):
    with caplog.at_level(logging.INFO, logger="app.pipeline.grouping"):
        group_articles(weighed(STRIKE_BBC, STRIKE_WSJ, LIVE_BLOG), GroupingConfig())
    join, story, summary = caplog.messages
    assert join.startswith(f'Cluster: joined "{STRIKE_BBC.title}" + "{STRIKE_WSJ.title}": average cosine ')
    assert story.startswith('Story "') and "(2 articles, cohesion " in story and "[WSJ]" in story
    assert summary.startswith(
        "Cluster (tf-idf, average linkage, threshold 0.20): 3 articles -> 2 stories "
        "(1 with more than one article, largest 2; 0 keyword rules matched; 0 vetoed merges) in "
    )


def test_cluster_logs_near_misses_at_debug(caplog):
    a, b = article("A", source="a"), article("B", source="b")
    a.vector, b.vector = {"x": 0.6, "y": 0.8, "z": 0.0}, {"x": 0.8, "y": 0.6, "z": 0.0}  # cosine 0.96
    with caplog.at_level(logging.DEBUG, logger="app.pipeline.grouping"):
        group_articles([a, b], GroupingConfig(tfidf_threshold=1.0))
    assert 'Cluster: near miss 0.96 < 1.00: "A" ~ "B"' in caplog.messages


def test_cluster_records_metrics():
    runs = metrics.CLUSTER_RUNS.labels("tf-idf")._value.get()
    group_articles(weighed(STRIKE_BBC, STRIKE_WSJ, LIVE_BLOG), GroupingConfig())
    assert metrics.CLUSTER_RUNS.labels("tf-idf")._value.get() == runs + 1
    assert metrics.CLUSTER_STORIES.labels("multi")._value.get() == 1
    assert metrics.CLUSTER_STORIES.labels("single")._value.get() == 1


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


def test_embed_articles_attaches_unit_vectors_in_batches_and_logs(caplog):
    articles = [article(f"Story {i}", source=f"s{i}", summary="body") for i in range(5)]
    llm = FakeLLM(embed=lambda text: [3.0, 4.0])
    runs = metrics.EMBED_RUNS.labels("success")._value.get()
    with caplog.at_level(logging.INFO, logger="app.pipeline.preprocess"):
        asyncio.run(embed_articles(articles, llm, EmbeddingConfig(model="m", batch_size=2)))
    assert [len(batch) for batch in llm.embed_calls] == [2, 2, 1]
    assert llm.embed_calls[0][0] == "Story 0\nbody"
    assert all(a.embedding == [0.6, 0.8] for a in articles)
    (line,) = caplog.messages
    assert line.startswith("Embed: 5 documents -> 5 embeddings (1 per document, 2 dimensions, 35 tokens) with m in ")
    assert line.endswith("per document, 3 request(s))")
    assert metrics.EMBED_RUNS.labels("success")._value.get() == runs + 1
    assert metrics.EMBED_DOCUMENTS._value.get() == 5 and metrics.EMBED_TOKENS._value.get() == 35


def test_embedding_failure_leaves_tfidf_in_charge(caplog):
    articles = [article("Story", source="s", summary="body")]
    failures = metrics.EMBED_RUNS.labels("failure")._value.get()
    with caplog.at_level(logging.WARNING, logger="app.pipeline.preprocess"):
        asyncio.run(embed_articles(articles, FakeLLM(), EmbeddingConfig(model="m")))
    assert articles[0].embedding is None
    assert "clustering on TF-IDF only" in caplog.messages[0]
    assert metrics.EMBED_RUNS.labels("failure")._value.get() == failures + 1


def _pair(tfidf_alike: bool, embedding_alike: bool):
    a, b = article("A", source="a"), article("B", source="b")
    shared = {"x1": 1.0, "x2": 1.0, "x3": 1.0}
    a.vector = {**shared} if tfidf_alike else {**shared, **{f"a{i}": 3.0 for i in range(9)}}
    b.vector = {**shared} if tfidf_alike else {**shared, **{f"b{i}": 3.0 for i in range(9)}}
    for x in (a, b):
        norm = sum(v * v for v in x.vector.values()) ** 0.5
        x.vector = {w: v / norm for w, v in x.vector.items()}
    a.embedding, b.embedding = [1.0, 0.0], ([0.995, 0.0998] if embedding_alike else [0.0, 1.0])
    return a, b


EMBEDDED = {"embeddings": EmbeddingConfig(model="m", threshold=0.8), "tfidf_threshold": 0.2}


def test_embeddings_decide_similarity_when_every_article_has_one():
    assert sizes(*_pair(tfidf_alike=False, embedding_alike=True), **EMBEDDED) == [2]
    assert sizes(*_pair(tfidf_alike=True, embedding_alike=False), **EMBEDDED) == [1, 1]


def test_tfidf_decides_when_embeddings_are_missing_or_disabled():
    a, b = _pair(tfidf_alike=True, embedding_alike=False)
    assert sizes(a, b, tfidf_threshold=0.2) == [2]  # embeddings not configured
    b.embedding = None
    assert sizes(a, b, **EMBEDDED) == [2]  # one article has none
