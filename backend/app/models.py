"""Data shapes shared by connectors, the pipeline and the API."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

DEFAULT_SITE_NAME = "PWS NEWS"


class SourceConfig(BaseModel):
    id: str
    name: str
    type: str
    url: str | None = None
    homepage: str | None = None  # publisher's site, linked from the sources page
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
    # Comparison keys derived in pipeline.preprocess; never serialised and never shown.
    # title/summary/url above pass through to the report untouched.
    url_key: str = Field(default="", exclude=True)
    words: frozenset[str] = Field(default_factory=frozenset, exclude=True)
    vector: dict[str, float] = Field(default_factory=dict, exclude=True)  # TF-IDF of title + summary
    embedding: list[float] | None = Field(default=None, exclude=True)  # unit length; only for this run


class Topic(BaseModel):
    """A bucket of articles judged to be about the same story."""

    name: str
    articles: list[Article]
    score: float = 0.0
    central: list[Article] = Field(default_factory=list)  # most central articles; the LLM writes from these


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
    # Defaults keep reports stored before these fields existed loadable (and titled).
    site_name: str = DEFAULT_SITE_NAME
    title: str = ""

    @model_validator(mode="after")
    def _default_title(self) -> "Report":
        if not self.title:
            self.title = f"{self.site_name} — {self.date}"
        return self
