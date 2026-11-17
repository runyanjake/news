"""Site visit counting: at most one view per visitor per local day.

    ViewCounts     persistent totals in SQLite: overall views and views by country (no per-visitor rows)
    DailyVisitors  who has been counted today, in Redis; keys expire, so the data is ephemeral
    CountryLookup  IP -> ISO country code from a local .mmdb file; no external service is called

Visitors are keyed by HMAC-SHA256(ip) under a random salt that changes every day, so raw IPs are never stored
and one day's keys can't be linked to another's.
"""

import hashlib
import hmac
import ipaddress
import logging
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import maxminddb
from redis import Redis

log = logging.getLogger(__name__)

_DAY_TTL = 2 * 24 * 3600  # outlives the local day in any timezone, then expires


class ViewCounts:
    """Overall views plus views per country; a view with no known country only counts in the total."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS totals (name TEXT PRIMARY KEY, views INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS country_views (country TEXT PRIMARY KEY, views INTEGER NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def add(self, country: str | None) -> None:
        with closing(self._connect()) as conn, conn as db:  # one transaction
            db.execute(
                "INSERT INTO totals VALUES ('views', 1) ON CONFLICT(name) DO UPDATE SET views = views + 1"
            )
            if country:
                db.execute(
                    "INSERT INTO country_views VALUES (?, 1) ON CONFLICT(country) DO UPDATE SET views = views + 1",
                    (country,),
                )

    def summary(self) -> dict:
        with closing(self._connect()) as db:
            total = db.execute("SELECT views FROM totals WHERE name = 'views'").fetchone()
            countries = db.execute("SELECT country, views FROM country_views ORDER BY views DESC, country").fetchall()
        return {"total": total[0] if total else 0, "by_country": dict(countries)}


class DailyVisitors:
    def __init__(self, redis: Redis, prefix: str = "news:visits"):
        self.redis = redis
        self.prefix = prefix

    def _salt(self, day: str) -> bytes:
        key = f"{self.prefix}:salt:{day}"
        self.redis.set(key, secrets.token_hex(32), nx=True, ex=_DAY_TTL)  # first visit of the day picks the salt
        salt = self.redis.get(key)
        return salt.encode() if isinstance(salt, str) else salt

    def first_today(self, ip: str, day: str) -> bool:
        """True the first time `ip` is seen on `day`, which also counts it towards today's views."""
        visitor = hmac.new(self._salt(day), ip.encode(), hashlib.sha256).hexdigest()
        if not self.redis.set(f"{self.prefix}:seen:{day}:{visitor}", 1, nx=True, ex=_DAY_TTL):
            return False
        today = f"{self.prefix}:views:{day}"
        self.redis.incr(today)
        self.redis.expire(today, _DAY_TTL)
        return True

    def views(self, day: str) -> int:
        return int(self.redis.get(f"{self.prefix}:views:{day}") or 0)


class CountryLookup:
    """ISO 3166-1 alpha-2 country of an IP; None when unknown, private, or the database is missing."""

    def __init__(self, path: Path | None):
        self.reader = None
        if path and Path(path).exists():
            self.reader = maxminddb.open_database(str(path))
        else:
            log.warning("No GeoIP database at %s; visits are counted without a country", path)

    def country(self, ip: str) -> str | None:
        if self.reader is None:
            return None
        try:
            record = self.reader.get(ipaddress.ip_address(ip))
        except ValueError:
            return None
        if not isinstance(record, dict):
            return None
        code = record.get("country_code") or (record.get("country") or {}).get("iso_code")
        return code.upper() if isinstance(code, str) and len(code) == 2 else None
