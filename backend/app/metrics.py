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
EMBED_RUNS = Counter(
    "news_embedding_runs", "Embedding steps by outcome (failure: clustering fell back to TF-IDF)", ["outcome"]
)
EMBED_DURATION = Histogram(
    "news_embedding_duration_seconds", "Wall time to embed one generation's articles",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
EMBED_DOCUMENTS = Gauge("news_embedding_documents", "Articles embedded in the last generation")
EMBED_TOKENS = Gauge("news_embedding_tokens", "Prompt tokens the embedding server reported in the last generation")
CLUSTER_RUNS = Counter("news_cluster_runs", "Clustering runs by vector kind", ["vectors"])
CLUSTER_DURATION = Histogram(
    "news_cluster_duration_seconds", "Wall time to cluster one generation's articles", ["vectors"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
CLUSTER_STORIES = Gauge("news_cluster_stories", "Stories from the last clustering, by size", ["size"])
CLUSTER_VETOES = Gauge("news_cluster_vetoes", "Merges vetoed for too few shared terms in the last clustering")
CLUSTER_COHESION = Histogram(
    "news_cluster_cohesion", "Average pairwise cosine within each multi-article story", ["vectors"],
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0),
)


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
