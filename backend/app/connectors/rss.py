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
_IMG_SRC = re.compile(r"""<img\b[^>]*?\bsrc=["']([^"']+)["']""", re.IGNORECASE)


def _clean(text: str) -> str:
    # Strip tags both before and after unescaping: feeds often double-encode markup.
    text = _TAG.sub(" ", html.unescape(_TAG.sub(" ", text or "")))
    return " ".join(text.split())


def _published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)


def _is_image(media: dict) -> bool:
    # Untyped media:content is almost always an image; video/audio say so via medium or type.
    medium, mime = media.get("medium", ""), media.get("type", "")
    return medium == "image" or mime.startswith("image/") or not (medium or mime)


def _width(media: dict) -> int:
    try:
        return int(media.get("width") or 0)
    except ValueError:
        return 0


def _image(entry) -> str | None:
    """Best image for an entry: the widest media:content, then a thumbnail, an image enclosure,
    or the first <img> in the item's HTML. Only absolute http(s) URLs are kept."""
    media = sorted((m for m in entry.get("media_content", []) if _is_image(m)), key=_width, reverse=True)
    candidates = [m.get("url") for m in media]
    candidates += [t.get("url") for t in entry.get("media_thumbnail", [])]
    candidates += [e.get("href") for e in entry.get("enclosures", []) if e.get("type", "").startswith("image/")]
    for markup in [entry.get("summary", "")] + [c.get("value", "") for c in entry.get("content", [])]:
        candidates += [html.unescape(src) for src in _IMG_SRC.findall(markup or "")]
    return next((u.strip() for u in candidates if u and re.match(r"https?://", u.strip(), re.I)), None)


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
                    image_url=_image(entry),
                )
            )
        return articles
