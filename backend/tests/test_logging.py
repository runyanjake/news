import logging
from datetime import UTC, datetime

from app.config import PipelineConfig, ReportConfig
from app.logs import LOG_FORMAT, UTCFormatter
from app.models import Report, ReportEntry, SourceRef, Topic
from app.pipeline.preprocess import preprocess
from app.pipeline.run import describe_report
from app.pipeline.selection import select_topics

from .conftest import NOW, article


def test_log_format_is_iso_utc_level_name_message():
    record = logging.LogRecord("app.main", logging.INFO, __file__, 1, "Job started", None, None)
    record.created, record.msecs = 0.0, 5
    assert UTCFormatter(LOG_FORMAT).format(record) == "1970-01-01T00:00:00.005Z INFO app.main: Job started"


def test_describe_report_lists_story_titles():
    ref = SourceRef(name="BBC", title="t", url="https://b.example", score=1)
    entry = lambda tagline, n: ReportEntry(  # noqa: E731
        topic="x", tagline=tagline, summary="", score=1, ai_generated=True, sources=[ref] * n
    )
    report = Report(date="2026-11-03", generated_at=datetime(2026, 11, 3, tzinfo=UTC),
                    entries=[entry("Senate passes budget", 3), entry("Rover finds lake", 1)])
    assert describe_report(report) == (
        'Generated report "PWS NEWS — 2026-11-03" (2 stories)\n'
        "  - Senate passes budget (3 sources)\n"
        "  - Rover finds lake (1 source)"
    )


def test_preprocess_logs_duplicate_links_and_sources(caplog):
    low = article("Story", source="low", reputation=0.3, url="https://x.example/s")
    high = article("Story", source="high", reputation=0.9, url="https://x.example/s?utm_source=rss")
    with caplog.at_level(logging.INFO, logger="app.pipeline.preprocess"):
        preprocess([[low], [high]], PipelineConfig(), NOW)
    assert caplog.messages == [
        "Preprocess: duplicate link x.example/s from LOW, HIGH; kept HIGH",
        "Preprocess: 2 items -> 1 articles (dropped 0 stale, 0 repeated within a feed, "
        "0 over the per-source cap, 1 duplicate links across feeds)",
    ]


def test_preprocess_drops_repeats_within_a_feed_before_the_cap(caplog):
    # A feed listing the same item twice (seen in the WSJ feed) must not use up a cap slot.
    story = article("Strikes on island", source="wsj", url="https://wsj.example/s?mod=rss")
    feed = [story, story.model_copy(), article("Pension covenant unravels", source="wsj")]
    with caplog.at_level(logging.INFO, logger="app.pipeline.preprocess"):
        kept = preprocess([feed], PipelineConfig(max_items_per_source=2), NOW)
    assert sorted(a.title for a in kept) == ["Pension covenant unravels", "Strikes on island"]
    assert caplog.messages == [
        'Preprocess: WSJ listed 1 item(s) more than once: "Strikes on island"',
        "Preprocess: 3 items -> 2 articles (dropped 0 stale, 1 repeated within a feed, "
        "0 over the per-source cap, 0 duplicate links across feeds)",
    ]


def test_select_logs_what_was_skipped(caplog):
    world = [Topic(name=f"W{i}", articles=[article(f"W{i}", tags=["world"])], score=9 - i) for i in range(3)]
    with caplog.at_level(logging.INFO, logger="app.pipeline.selection"):
        select_topics(world, ReportConfig(max_entries=5, max_entries_per_tag=1))
    assert caplog.messages == ["Select: 1 of 3 stories (1 section leads; skipped 2 over the per-section cap of 1 (world 2))"]


def test_report_title_comes_from_the_backend():
    stamp = datetime(2026, 11, 3, tzinfo=UTC)
    assert Report(date="2026-11-03", generated_at=stamp, entries=[], site_name="X").title == "X — 2026-11-03"
    # Reports stored before site_name/title existed still load with both set.
    old = Report.model_validate_json('{"date": "2026-10-01", "generated_at": "2026-10-01T08:00:00Z", "entries": []}')
    assert (old.site_name, old.title) == ("PWS NEWS", "PWS NEWS — 2026-10-01")
