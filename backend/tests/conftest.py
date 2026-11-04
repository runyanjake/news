from datetime import UTC, datetime, timedelta

import pytest

from app.connectors import Connector
from app.llm import LLMError
from app.models import Article, SourceConfig
from app.pipeline.preprocess import annotate
from app.settings import Settings

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)


def article(title, source="a", reputation=0.5, hours_old=1.0, url=None, summary="", tags=()):
    """An annotated article, as stages after preprocess expect."""
    return annotate(Article(
        tags=list(tags),
        source_id=source,
        source_name=source.upper(),
        title=title,
        url=url or f"https://{source}.example/{abs(hash(title))}",
        summary=summary,
        published_at=NOW - timedelta(hours=hours_old),
        reputation=reputation,
    ))


class StaticConnector(Connector):
    def __init__(self, source_id, articles, reputation=0.5):
        super().__init__(SourceConfig(id=source_id, name=source_id.upper(), type="static", reputation=reputation))
        self.articles = articles

    async def fetch(self, client):
        return self.articles


class FailingConnector(Connector):
    def __init__(self):
        super().__init__(SourceConfig(id="broken", name="Broken", type="static"))

    async def fetch(self, client):
        raise RuntimeError("boom")


class FakeLLM:
    def __init__(self, response=None, fail=False, embed=None):
        self.response = response or {"tagline": "AI tagline", "summary": "AI summary."}
        self.fail = fail
        self.embed_fn = embed  # text -> vector; None means the server has no embeddings
        self.calls = []
        self.embed_calls = []

    async def complete_json(self, system, user):
        self.calls.append(user)
        if self.fail:
            raise LLMError("offline")
        return self.response

    async def embed(self, texts, model):
        self.embed_calls.append(list(texts))
        if self.fail or self.embed_fn is None:
            raise LLMError("no embeddings")
        return [self.embed_fn(t) for t in texts], 7 * len(texts)


@pytest.fixture
def fake_llm():
    return FakeLLM()


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
