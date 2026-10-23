"""FastAPI app: serves stored reports and triggers generation."""

import asyncio
import logging
import secrets
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException

from .config import load_config
from .llm import LLMClient
from .models import Report
from .pipeline import generate_report
from .settings import Settings, load_settings
from .storage import ReportStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = ReportStore(settings.reports_dir)
    llm = llm or LLMClient(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout
    )
    app = FastAPI(title="PWS News")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

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
            raise HTTPException(400, str(exc))
        if report is None:
            raise HTTPException(404, f"No report for {date}")
        return report

    @app.get("/api/reports/{date}/generations")
    def list_generations(date: str) -> list[str]:
        try:
            return store.generations(date)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/reports/{date}/generations/{stamp}", response_model=Report)
    def get_generation(date: str, stamp: str) -> Report:
        try:
            report = store.get_generation(date, stamp)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if report is None:
            raise HTTPException(404, f"No generation {stamp} for {date}")
        return report

    job = {"running": False, "last_date": None, "last_error": None}
    tasks: set[asyncio.Task] = set()

    async def run_generation() -> None:
        try:
            # Re-read config each run so source edits apply without a restart.
            config = load_config(settings.sources_file)
            local_date = datetime.now().astimezone().date().isoformat()
            store.save(await generate_report(config, llm, local_date))
            job.update(last_date=local_date, last_error=None)
        except Exception as exc:  # surfaced to the caller via the job status
            logging.exception("Report generation failed")
            job["last_error"] = str(exc)
        finally:
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
