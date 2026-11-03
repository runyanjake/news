"""Tiny text helpers shared by grouping and scoring."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

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
