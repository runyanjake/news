"""FastAPI app: serves stored reports and triggers generation."""

import asyncio
import logging
import secrets
import time
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException, Response

from . import metrics
from .config import load_config
from .llm import LLMClient
from .logs import configure_logging
from .models import Report
from .pipeline import generate_report
from .settings import Settings, load_settings
from .storage import ReportStore

configure_logging()
log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    configure_logging()  # again: uvicorn configures its loggers after importing this module
    settings = settings or load_settings()
    store = ReportStore(settings.reports_dir)
    llm = llm or LLMClient(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout,
        settings.embedding_base_url,
    )
    app = FastAPI(title="PWS News")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        """Prometheus scrape target; nginx doesn't proxy it, so only the internal network can reach it."""
        body, content_type = metrics.exposition()
        return Response(body, media_type=content_type)

    @app.get("/api/sources")
    def list_sources() -> list[dict]:
        """Enabled sources, for the site's source list; feed URLs and options stay private."""
        config = load_config(settings.sources_file)
        return [
            {"name": s.name, "tags": s.tags, "homepage": s.homepage}
            for s in config.sources
            if s.enabled
        ]

    @app.get("/api/reports")
    def list_reports() -> list[str]:
        return store.dates()

    @app.get("/api/reports/latest", response_model=Report)
    def latest_report() -> Report:
        report = store.latest()
        if report is None:
            raise HTTPException(404, "No reports yet")
        return report

    @app.get("/api/reports/{date}", response_model=Report)
    def get_report(date: str) -> Report:
        try:
            report = store.get(date)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if report is None:
            raise HTTPException(404, f"No report for {date}")
        return report

    @app.get("/api/reports/{date}/generations")
    def list_generations(date: str) -> list[str]:
        try:
            return store.generations(date)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/reports/{date}/generations/{stamp}", response_model=Report)
    def get_generation(date: str, stamp: str) -> Report:
        try:
            report = store.get_generation(date, stamp)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if report is None:
            raise HTTPException(404, f"No generation {stamp} for {date}")
        return report

    job = {"running": False, "last_date": None, "last_error": None}
    tasks: set[asyncio.Task] = set()

    async def run_generation() -> None:
        started = time.monotonic()
        log.info("Generation job started")
        try:
            # Re-read config each run so source edits apply without a restart.
            config = load_config(settings.sources_file)
            local_date = datetime.now().astimezone().date().isoformat()
            report = await generate_report(config, llm, local_date)
            store.save(report)
            job.update(last_date=local_date, last_error=None)
            metrics.GENERATION_RUNS.labels("success").inc()
            metrics.LAST_SUCCESS.set_to_current_time()
            log.info(
                "Generation job finished in %.1fs: %s, %d stories",
                time.monotonic() - started, local_date, len(report.entries),
            )
        except Exception as exc:  # surfaced to the caller via the job status
            log.exception("Generation job failed after %.1fs", time.monotonic() - started)
            job["last_error"] = str(exc)
            metrics.GENERATION_RUNS.labels("failure").inc()
        finally:
            metrics.GENERATION_DURATION.observe(time.monotonic() - started)
            job["running"] = False

    @app.post("/api/reports/generate", status_code=202)
    async def generate(x_admin_token: str = Header(default="")) -> dict:
        """Start a generation run; poll GET /api/jobs/generate until it finishes."""
        if not settings.admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
            raise HTTPException(403, "Invalid admin token")
        if job["running"]:
            raise HTTPException(409, "A report is already being generated")
        job["running"] = True
        task = asyncio.create_task(run_generation())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return job

    @app.get("/api/jobs/generate")
    def generation_status() -> dict:
        return job

    return app
