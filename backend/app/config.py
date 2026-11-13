"""Loads config/sources.yaml, expanding ${ENV_VAR} references."""

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import DEFAULT_SITE_NAME, SourceConfig

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


class TopicRule(BaseModel):
    name: str
    keywords: list[str]


class PipelineConfig(BaseModel):
    max_age_hours: float = 36
    max_items_per_source: int = 30


class EmbeddingConfig(BaseModel):
    model: str  # served at EMBEDDING_BASE_URL (default: the LLM server)
    threshold: float = 0.8  # average embedding cosine at which two clusters are one story
    batch_size: int = 64


class GroupingConfig(BaseModel):
    tfidf_threshold: float = 0.2  # average TF-IDF cosine, used when embeddings are off or fail
    embeddings: EmbeddingConfig | None = None
    central_articles: int = 4  # most central articles of a story the LLM writes its headline from
    topics: list[TopicRule] = Field(default_factory=list)


class StoryScoringConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {"coverage": 0.6, "lead": 0.3, "breadth": 0.1}
    )
    coverage_half: float = 1.5  # reputation-weighted sources that earn half the coverage credit


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(  # per article
        default_factory=lambda: {"reputation": 0.5, "recency": 0.3, "relevance": 0.2}
    )
    recency_half_life_hours: float = 12
    story: StoryScoringConfig = Field(default_factory=StoryScoringConfig)


class ReportConfig(BaseModel):
    max_entries: int = 15
    min_entries: int | None = None  # None: always max_entries (when there are enough stories)
    # Past min_entries, story k needs a score that rises from cutoff_start (k = min_entries + 1)
    # to cutoff_end (k = max_entries); selection stops at the first story below it.
    cutoff_start: float = 0.5
    cutoff_end: float = 0.6
    cover_sections: bool = True  # take each section's best story before filling by score
    sources_per_entry: int = 5
    min_sources_per_entry: int = 1
    max_entries_per_tag: int | None = None
    site_name: str = DEFAULT_SITE_NAME  # report title is "<site_name> — <date>"

    @model_validator(mode="after")
    def _entry_bounds(self) -> "ReportConfig":
        if self.min_entries is None:
            self.min_entries = self.max_entries
        if not 0 <= self.min_entries <= self.max_entries:
            raise ValueError("report.min_entries must be between 0 and max_entries")
        return self


class AppConfig(BaseModel):
    sources: list[SourceConfig]
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    grouping: GroupingConfig = Field(default_factory=GroupingConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)


def _expand_env(value):
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return AppConfig.model_validate(_expand_env(raw))
