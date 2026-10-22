"""Runtime settings read from the environment (populated from .env)."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    sources_file: Path
    reports_dir: Path
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_timeout: float
    admin_token: str


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        sources_file=Path(env("SOURCES_FILE", REPO_ROOT / "config" / "sources.yaml")),
        reports_dir=Path(env("REPORTS_DIR", REPO_ROOT / "data" / "reports")),
        llm_base_url=env("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
        llm_model=env("LLM_MODEL", "llama3.1"),
        llm_api_key=env("LLM_API_KEY", ""),
        llm_timeout=float(env("LLM_TIMEOUT_SECONDS", "120")),
        admin_token=env("ADMIN_TOKEN", ""),
    )
