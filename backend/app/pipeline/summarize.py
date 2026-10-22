"""Turn a ranked topic into a report entry with an AI tagline and summary."""

import logging

from ..llm import LLMClient, LLMError
from ..models import Article, ReportEntry, SourceRef, Topic

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the editor of a terse, Drudge-style news report. Given several source "
    "articles about one story, reply with JSON only: "
    '{"tagline": "punchy headline, max 12 words", "summary": "2-3 neutral sentences '
    'stating what happened, using only facts present in the sources"}.'
)


def _prompt(articles: list[Article]) -> str:
    return "\n\n".join(
        f"Source: {a.source_name}\nTitle: {a.title}\nExcerpt: {a.summary[:600]}" for a in articles
    )


async def summarize_topic(topic: Topic, top: list[Article], llm: LLMClient | None) -> ReportEntry:
    tagline, summary, ai = top[0].title, top[0].summary[:400], False
    if llm is not None:
        try:
            result = await llm.complete_json(SYSTEM_PROMPT, _prompt(top))
            tagline = str(result.get("tagline") or tagline).strip()
            summary = str(result.get("summary") or summary).strip()
            ai = True
        except LLMError as exc:
            log.warning("LLM summary failed for %r, using top source: %s", topic.name, exc)
    return ReportEntry(
        topic=topic.name,
        tagline=tagline,
        summary=summary,
        score=topic.score,
        ai_generated=ai,
        sources=[SourceRef(name=a.source_name, title=a.title, url=a.url, score=a.score) for a in top],
    )
