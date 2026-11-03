"""Tiny text helpers shared by preprocessing, grouping and scoring.

These only derive comparison keys; they never change the text shown in a report.
"""

import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_WORD = re.compile(r"[a-z0-9']+")
STOPWORDS = frozenset(
    """a an and are as at be but by for from has have he her his in into is it its
    of on or over says said she that the their they this to up was were what when
    who will with after new how why you your not no more than about out amid""".split()
)


_ACRONYM = re.compile(r"\b(?:[a-z]\.){2,}")  # "u.s." / "u.k." -> "us" / "uk"


def _normalise(word: str) -> str:
    """Fold possessives and plain plurals so "Iran's"/"Iran" and "strikes"/"strike" match."""
    word = word.strip("'")
    word = word.removesuffix("'s")
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    return word


def tokens(text: str) -> set[str]:
    text = _ACRONYM.sub(lambda m: m.group().replace(".", ""), text.lower().replace("’", "'"))
    words = (_normalise(w) for w in _WORD.findall(text))
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


Vector = dict[str, float]


def tfidf_vectors(docs: list[tuple[set[str], set[str]]], title_weight: float = 2.0) -> list[Vector]:
    """Unit-length TF-IDF vectors for (title words, body words) documents.

    Words common across the day's coverage (e.g. "election" on election day) weigh
    little; rare shared words (names, places) carry the match. Title words count extra.
    """
    df: dict[str, int] = {}
    for title, body in docs:
        for word in title | body:
            df[word] = df.get(word, 0) + 1
    n = len(docs)
    vectors = []
    for title, body in docs:
        vec = {w: math.log(n / df[w]) * (title_weight if w in title else 1.0) for w in title | body}
        norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
        vectors.append({w: x / norm for w, x in vec.items() if x})
    return vectors


def add_into(total: Vector, vec: Vector) -> None:
    for word, x in vec.items():
        total[word] = total.get(word, 0.0) + x


def cosine(a: Vector, b: Vector) -> float:
    if len(a) > len(b):
        a, b = b, a
    dot = sum(x * b.get(w, 0.0) for w, x in a.items())
    norms = math.sqrt(sum(x * x for x in a.values())) * math.sqrt(sum(x * x for x in b.values()))
    return dot / norms if norms else 0.0


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_TRACKING_PREFIXES = ("utm_", "at_", "mc_")
_TRACKING_PARAMS = frozenset({"cmp", "ref", "fbclid", "gclid", "ocid", "src", "rss"})


def _is_tracking(param: str) -> bool:
    param = param.lower()
    return param in _TRACKING_PARAMS or param.startswith(_TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Normalise a link so feed variants of one article compare equal.

    Drops scheme, "www.", fragment, trailing slash and tracking params (utm_*, etc.).
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not _is_tracking(k)]
    )
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")
