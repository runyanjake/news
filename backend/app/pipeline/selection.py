"""Pick the report's stories from the ranked list: how many, and which.

1. Section leads: each section's best story gets in first, so every section is covered.
2. Fill by score, at most max_entries_per_tag per section, up to max_entries. Past
   min_entries each further story must clear a cutoff that rises with the report's length
   (cutoff_start for the first extra story up to cutoff_end for the last), so a busy day
   gets a longer report and a quiet one stops early.
"""

import logging
from collections import Counter

from ..config import ReportConfig
from ..models import Article, Topic
from .scoring import best_per_source, section_of

log = logging.getLogger(__name__)

Story = tuple[Topic, list[Article]]  # a ranked topic and the top sources shown for it


def story_section(top: list[Article]) -> str | None:
    """The section most of the story's sources belong to; ties go to the better source."""
    counts = Counter(s for a in top if (s := section_of(a)))
    return counts.most_common(1)[0][0] if counts else None


def cutoff(position: int, config: ReportConfig) -> float | None:
    """Score the story at this 1-based position needs, or None within min_entries."""
    if position <= config.min_entries:
        return None
    extra = config.max_entries - config.min_entries
    if extra <= 1:
        return config.cutoff_start
    step = (config.cutoff_end - config.cutoff_start) / (extra - 1)
    return config.cutoff_start + step * (position - config.min_entries - 1)


def select_topics(ranked: list[Topic], config: ReportConfig) -> list[Story]:
    """Best topics with their top sources, in rank order."""
    cap = config.max_entries_per_tag
    candidates, thin = [], 0
    for topic in ranked:
        top = best_per_source(topic)[: config.sources_per_entry]
        if len(top) < config.min_sources_per_entry:
            thin += 1
            continue
        candidates.append((topic, top, story_section(top)))

    chosen: set[int] = set()
    per_tag: Counter = Counter()

    def take(i: int, section: str | None) -> None:
        chosen.add(i)
        per_tag[section] += 1

    if config.cover_sections:
        for i, (_, _, section) in enumerate(candidates):
            if len(chosen) == config.max_entries:
                break
            if section and section not in per_tag:
                take(i, section)
    leads = len(chosen)

    capped, stop = Counter(), None
    for i, (topic, _, section) in enumerate(candidates):
        if len(chosen) == config.max_entries:
            break
        if i in chosen:
            continue
        if section and cap is not None and per_tag[section] >= cap:
            capped[section] += 1
            continue
        needed = cutoff(len(chosen) + 1, config)
        if needed is not None and topic.score < needed:
            stop = f"story {len(chosen) + 1} scored {topic.score:.2f}, below the cutoff of {needed:.2f}"
            break
        take(i, section)

    notes = [f"{leads} section leads"] if leads else []
    if stop:
        notes.append(f"stopped: {stop}")
    skipped = [f"{thin} with fewer than {config.min_sources_per_entry} sources"] if thin else []
    if capped:
        by_tag = ", ".join(f"{tag} {n}" for tag, n in capped.most_common())
        skipped.append(f"{capped.total()} over the per-section cap of {cap} ({by_tag})")
    if skipped:
        notes.append(f"skipped {'; '.join(skipped)}")
    log.info("Select: %d of %d stories%s", len(chosen), len(ranked), f" ({'; '.join(notes)})" if notes else "")
    return [(topic, top) for i, (topic, top, _) in enumerate(candidates) if i in chosen]
