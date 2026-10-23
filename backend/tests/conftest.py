from datetime import UTC, datetime, timedelta

import pytest

from app.connectors import Connector
from app.llm import LLMError
from app.models import Article, SourceConfig

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)


def article(title, source="a", reputation=0.5, hours_old=1.0, url=None, summary="", tags=()):
    return Article(
        tags=list(tags),
        source_id=source,
        source_name=source.upper(),
        title=title,
        url=url or f"https://{source}.example/{abs(hash(title))}",
        summary=summary,
        published_at=NOW - timedelta(hours=hours_old),
        reputation=reputation,
    )


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
    def __init__(self, response=None, fail=False):
        self.response = response or {"tagline": "AI tagline", "summary": "AI summary."}
        self.fail = fail
        self.calls = []

    async def complete_json(self, system, user):
        self.calls.append(user)
        if self.fail:
            raise LLMError("offline")
        return self.response


@pytest.fixture
def fake_llm():
    return FakeLLM()
