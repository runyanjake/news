"""Loads config/sources.yaml, expanding ${ENV_VAR} references."""

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import SourceConfig

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


class TopicRule(BaseModel):
    name: str
    keywords: list[str]


class PipelineConfig(BaseModel):
    max_age_hours: float = 36
    max_items_per_source: int = 30


class GroupingConfig(BaseModel):
    method: str = "keywords"
    similarity_threshold: float = 0.25
    topics: list[TopicRule] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {"reputation": 0.5, "recency": 0.3, "relevance": 0.2}
    )
    recency_half_life_hours: float = 12


class ReportConfig(BaseModel):
    max_entries: int = 15
    sources_per_entry: int = 5
    min_sources_per_entry: int = 1


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
