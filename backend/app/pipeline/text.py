"""Tiny text helpers shared by grouping and scoring."""

import re

_WORD = re.compile(r"[a-z0-9']+")
STOPWORDS = frozenset(
    """a an and are as at be but by for from has have he her his in into is it its
    of on or over says said she that the their they this to up was were what when
    who will with after new how why you your not no more than about out amid""".split()
)


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
