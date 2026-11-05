"""Prometheus metrics, served in the text exposition format at GET /metrics."""

import time
from contextlib import contextmanager

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)

disable_created_metrics()  # no `*_created` series alongside every counter and histogram

GENERATION_RUNS = Counter(
    "news_generation_runs", "Report generation runs by outcome", ["outcome"]
)
GENERATION_DURATION = Histogram(
    "news_generation_duration_seconds", "Report generation wall time",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
LAST_SUCCESS = Gauge(
    "news_generation_last_success_timestamp_seconds", "Unix time of the last successful generation"
)
REPORT_ENTRIES = Gauge(
    "news_report_entries", "Entries in the last generated report, by who wrote them", ["writer"]
)
SOURCE_FETCHES = Counter(
    "news_source_fetches", "Source fetches by source and outcome", ["source", "outcome"]
)
SOURCE_ITEMS = Gauge(
    "news_source_items", "Items fetched from each source in the last generation", ["source"]
)
LLM_REQUESTS = Counter(
    "news_llm_requests", "LLM API requests by operation and outcome", ["operation", "outcome"]
)
LLM_DURATION = Histogram(
    "news_llm_request_duration_seconds", "LLM API request latency", ["operation"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

# Create every labelled series at 0 up front: a series that first appears at 1 is invisible to increase(),
# so the first run after a restart (often the only one that day) would not be counted.
for _outcome in ("success", "failure"):
    GENERATION_RUNS.labels(_outcome)
for _writer in ("llm", "fallback"):
    REPORT_ENTRIES.labels(_writer)
for _operation in ("chat", "embed"):
    LLM_DURATION.labels(_operation)
    for _outcome in ("success", "error"):
        LLM_REQUESTS.labels(_operation, _outcome)


def track_sources(source_ids) -> None:
    """Create the per-source series at 0, so a source's first failure registers as an increase."""
    for source_id in source_ids:
        SOURCE_ITEMS.labels(source_id)
        for outcome in ("success", "failure"):
            SOURCE_FETCHES.labels(source_id, outcome)


@contextmanager
def llm_request(operation: str):
    """Time one LLM API call and count it as an error if the block raises."""
    started = time.monotonic()
    outcome = "error"
    try:
        yield
        outcome = "success"
    finally:
        LLM_DURATION.labels(operation).observe(time.monotonic() - started)
        LLM_REQUESTS.labels(operation, outcome).inc()


def exposition() -> tuple[bytes, str]:
    """The current metrics and their content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
