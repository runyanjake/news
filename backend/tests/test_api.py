import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import create_app
from app.models import Report
from app.settings import Settings
from app.storage import ReportStore


@pytest.fixture
def settings(tmp_path):
    sources = tmp_path / "sources.yaml"
    sources.write_text("sources: []\n")
    return Settings(
        sources_file=sources,
        reports_dir=tmp_path / "reports",
        llm_base_url="http://unused",
        llm_model="m",
        llm_api_key="",
        llm_timeout=1,
        admin_token="tok",
    )


def _report(date):
    return Report(date=date, generated_at=datetime(2026, 1, 1, tzinfo=UTC), entries=[])


def test_reports_history(settings):
    store = ReportStore(settings.reports_dir)
    store.save(_report("2026-10-21"))
    store.save(_report("2026-10-22"))
    client = TestClient(create_app(settings))

    assert client.get("/api/reports").json() == ["2026-10-22", "2026-10-21"]
    assert client.get("/api/reports/latest").json()["date"] == "2026-10-22"
    assert client.get("/api/reports/2026-10-21").json()["date"] == "2026-10-21"
    assert client.get("/api/reports/2020-01-01").status_code == 404
    assert client.get("/api/reports/..%2Fsecrets").status_code in (400, 404)


def test_latest_404_when_empty(settings):
    assert TestClient(create_app(settings)).get("/api/reports/latest").status_code == 404


def test_generate_requires_admin_token(settings):
    client = TestClient(create_app(settings))
    assert client.post("/api/reports/generate").status_code == 403
    assert client.post("/api/reports/generate", headers={"X-Admin-Token": "nope"}).status_code == 403


def _wait_for_job(client):
    for _ in range(100):
        status = client.get("/api/jobs/generate").json()
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("generation did not finish")


def test_generate_runs_in_background_and_saves_report(settings, monkeypatch):
    async def fake_generate(config, llm, local_date):
        return _report(local_date)

    monkeypatch.setattr(main, "generate_report", fake_generate)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/reports/generate", headers={"X-Admin-Token": "tok"})
        assert response.status_code == 202
        status = _wait_for_job(client)

        assert status["last_error"] is None
        assert client.get("/api/reports").json() == [status["last_date"]]


def test_generate_failure_is_reported(settings, monkeypatch):
    async def broken(config, llm, local_date):
        raise RuntimeError("all feeds down")

    monkeypatch.setattr(main, "generate_report", broken)
    with TestClient(create_app(settings)) as client:
        client.post("/api/reports/generate", headers={"X-Admin-Token": "tok"})
        assert _wait_for_job(client)["last_error"] == "all feeds down"
