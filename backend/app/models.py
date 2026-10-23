"""Data shapes shared by connectors, the pipeline and the API."""

from datetime import datetime

from pydantic import BaseModel, Field


class SourceConfig(BaseModel):
    id: str
    name: str
    type: str
    url: str | None = None
    reputation: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    options: dict = Field(default_factory=dict)


class Article(BaseModel):
    """One item pulled from a source, normalised across connector types."""

    source_id: str
    source_name: str
    title: str
    url: str
    summary: str = ""
    published_at: datetime | None = None
    reputation: float = 0.5
    tags: list[str] = Field(default_factory=list)
    score: float = 0.0


class Topic(BaseModel):
    """A bucket of articles judged to be about the same story."""

    name: str
    articles: list[Article]
    score: float = 0.0


class SourceRef(BaseModel):
    name: str
    title: str
    url: str
    score: float


class ReportEntry(BaseModel):
    topic: str
    tagline: str
    summary: str
    score: float
    ai_generated: bool
    sources: list[SourceRef]


class Report(BaseModel):
    date: str  # local date, YYYY-MM-DD
    generated_at: datetime
    entries: list[ReportEntry]
