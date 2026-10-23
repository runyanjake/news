"""RSS / Atom connector."""

import calendar
import html
import re
from datetime import UTC, datetime

import feedparser
import httpx

from ..models import Article
from .base import Connector, register

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    # Strip tags both before and after unescaping: feeds often double-encode markup.
    text = _TAG.sub(" ", html.unescape(_TAG.sub(" ", text or "")))
    return " ".join(text.split())


def _published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)


@register("rss")
class RSSConnector(Connector):
    async def fetch(self, client: httpx.AsyncClient) -> list[Article]:
        response = await client.get(self.source.url, follow_redirects=True)
        response.raise_for_status()
        return self.parse(response.content)

    def parse(self, content: bytes | str) -> list[Article]:
        feed = feedparser.parse(content)
        articles = []
        for entry in feed.entries:
            title, link = _clean(entry.get("title", "")), entry.get("link")
            if not title or not link:
                continue
            articles.append(
                self.make_article(
                    title=title,
                    url=link,
                    summary=_clean(entry.get("summary", ""))[:1000],
                    published_at=_published(entry),
                )
            )
        return articles
