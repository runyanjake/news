import asyncio

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app import main, metrics
from app.llm import LLMClient, LLMError
from app.main import create_app
from app.models import Topic
from app.pipeline.run import collect, write_entries

from .conftest import FailingConnector, FakeLLM, StaticConnector, article
from .test_api import _report, _wait_for_job


def sample(name, **labels):
    """Current value of a metric sample; metrics are process-global, so tests compare deltas."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_endpoint_serves_prometheus_text(settings):
    response = TestClient(create_app(settings)).get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE news_generation_runs_total counter" in response.text
    assert "# TYPE news_llm_request_duration_seconds histogram" in response.text


def test_generation_outcomes_are_counted(settings, monkeypatch):
    async def fake_generate(config, llm, local_date):
        return _report(local_date)

    async def broken(config, llm, local_date):
        raise RuntimeError("all feeds down")

    ok, failed = sample("news_generation_runs_total", outcome="success"), sample("news_generation_runs_total", outcome="failure")
    runs = sample("news_generation_duration_seconds_count")
    with TestClient(create_app(settings)) as client:
        for generate in (fake_generate, broken):
            monkeypatch.setattr(main, "generate_report", generate)
            client.post("/api/reports/generate", headers={"X-Admin-Token": "tok"})
            _wait_for_job(client)

    assert sample("news_generation_runs_total", outcome="success") == ok + 1
    assert sample("news_generation_runs_total", outcome="failure") == failed + 1
    assert sample("news_generation_duration_seconds_count") == runs + 2
    assert sample("news_generation_last_success_timestamp_seconds") > 0


def test_collect_records_fetches_per_source():
    before = sample("news_source_fetches_total", source="broken", outcome="failure")
    connectors = [StaticConnector("m1", [article("One", source="m1"), article("Two", source="m1")]), FailingConnector()]
    asyncio.run(collect(connectors))

    assert sample("news_source_items", source="m1") == 2
    assert sample("news_source_fetches_total", source="m1", outcome="success") >= 1
    assert sample("news_source_fetches_total", source="broken", outcome="failure") == before + 1
    assert sample("news_source_items", source="broken") == 0


def test_write_entries_records_fallbacks():
    a = article("Lone headline", source="a")
    stories = [(Topic(name="t", articles=[a]), [a])]
    asyncio.run(write_entries(stories, FakeLLM(fail=True)))
    assert (sample("news_report_entries", writer="llm"), sample("news_report_entries", writer="fallback")) == (0, 1)
    asyncio.run(write_entries(stories, FakeLLM()))
    assert (sample("news_report_entries", writer="llm"), sample("news_report_entries", writer="fallback")) == (1, 0)


def test_llm_request_counts_errors_and_latency():
    errors = sample("news_llm_requests_total", operation="chat", outcome="error")
    observed = sample("news_llm_request_duration_seconds_count", operation="chat")
    client = LLMClient("http://127.0.0.1:9/v1", "m", timeout=1)  # nothing listens on the discard port
    with pytest.raises(LLMError):
        asyncio.run(client.complete("system", "user"))

    assert sample("news_llm_requests_total", operation="chat", outcome="error") == errors + 1
    assert sample("news_llm_request_duration_seconds_count", operation="chat") == observed + 1

    ok = sample("news_llm_requests_total", operation="embed", outcome="success")
    with metrics.llm_request("embed"):
        pass
    assert sample("news_llm_requests_total", operation="embed", outcome="success") == ok + 1


def test_series_exist_before_the_first_event(settings):
    settings.sources_file.write_text("sources:\n  - {id: fresh, name: Fresh, type: rss, url: 'https://f/feed'}\n")
    text = TestClient(create_app(settings)).get("/metrics").text
    assert 'news_source_fetches_total{outcome="failure",source="fresh"}' in text
    assert 'news_generation_runs_total{outcome="failure"}' in text
    assert 'news_llm_requests_total{operation="embed",outcome="error"}' in text
