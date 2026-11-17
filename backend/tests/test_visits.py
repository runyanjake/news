import sqlite3

import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis import RedisError

from app import main
from app.main import create_app
from app.visits import CountryLookup, DailyVisitors, ViewCounts

from .test_metrics import sample

COUNTRIES = {"8.8.8.8": "US", "81.2.69.142": "GB"}


class FakeLookup:
    def __init__(self, path):
        pass

    def country(self, ip):
        return COUNTRIES.get(ip)


class DownRedis(fakeredis.FakeRedis):
    def execute_command(self, *args, **kwargs):
        raise RedisError("connection refused")


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setattr(main, "CountryLookup", FakeLookup)
    return TestClient(create_app(settings, redis=fakeredis.FakeRedis()))


def visit(client, ip):
    assert client.post("/api/visits", headers={"X-Real-IP": ip}).status_code == 204


def test_each_visitor_counts_once_per_day(client):
    for ip in ("8.8.8.8", "8.8.8.8", "81.2.69.142", "8.8.8.8", "10.0.0.7"):
        visit(client, ip)
    assert client.get("/api/analytics").json() == {"total": 3, "today": 3, "by_country": {"GB": 1, "US": 1}}


def test_analytics_is_never_cached(client):
    assert client.get("/api/analytics").headers["cache-control"] == "no-store"
    visit(client, "8.8.8.8")
    assert client.get("/api/analytics").json()["total"] == 1


def test_next_day_counts_again(client, monkeypatch):
    visit(client, "8.8.8.8")
    monkeypatch.setattr(main, "_today", lambda: "2099-01-01")
    visit(client, "8.8.8.8")
    assert client.get("/api/analytics").json() == {"total": 2, "today": 1, "by_country": {"US": 2}}


def test_counts_persist_across_restarts(settings, monkeypatch):
    monkeypatch.setattr(main, "CountryLookup", FakeLookup)
    visit(TestClient(create_app(settings, redis=fakeredis.FakeRedis())), "8.8.8.8")
    restarted = TestClient(create_app(settings, redis=fakeredis.FakeRedis()))
    assert restarted.get("/api/analytics").json()["total"] == 1


def test_raw_ips_are_never_stored(settings, monkeypatch):
    monkeypatch.setattr(main, "CountryLookup", FakeLookup)
    redis = fakeredis.FakeRedis()
    visit(TestClient(create_app(settings, redis=redis)), "8.8.8.8")
    stored = "\n".join(sqlite3.connect(settings.analytics_db).iterdump())
    stored += "\n".join(f"{k} {redis.get(k)}" for k in redis.keys())
    assert "8.8.8.8" not in stored


def test_redis_keys_hold_only_hashes_and_expire():
    redis = fakeredis.FakeRedis()
    DailyVisitors(redis).first_today("8.8.8.8", "2026-11-17")
    keys = [k.decode() for k in redis.keys()]
    assert keys and not any("8.8.8.8" in k for k in keys)
    assert all(0 < redis.ttl(k) <= 2 * 24 * 3600 for k in keys)


def test_daily_salt_unlinks_days():
    redis = fakeredis.FakeRedis()
    visitors = DailyVisitors(redis)
    visitors.first_today("8.8.8.8", "2026-11-17")
    visitors.first_today("8.8.8.8", "2026-11-18")
    hashes = {k.decode().rsplit(":", 1)[1] for k in redis.keys("news:visits:seen:*")}
    assert len(hashes) == 2


def test_redis_down_skips_counting(settings, monkeypatch):
    monkeypatch.setattr(main, "CountryLookup", FakeLookup)
    client = TestClient(create_app(settings, redis=DownRedis()))
    errors = sample("news_visits_total", outcome="error")
    visit(client, "8.8.8.8")
    assert client.get("/api/analytics").json() == {"total": 0, "today": None, "by_country": {}}
    assert sample("news_visits_total", outcome="error") == errors + 1


def test_visits_without_country_count_in_total_only(tmp_path):
    counts = ViewCounts(tmp_path / "v.db")
    counts.add(None)
    counts.add("US")
    assert counts.summary() == {"total": 2, "by_country": {"US": 1}}


def test_country_lookup_without_database(tmp_path):
    lookup = CountryLookup(tmp_path / "missing.mmdb")
    assert lookup.country("8.8.8.8") is None


def test_country_lookup_reads_country_code(tmp_path):
    lookup = CountryLookup(None)
    lookup.reader = type("Reader", (), {"get": lambda self, ip: COUNTRIES.get(str(ip)) and {"country_code": "us"}})()
    assert lookup.country("8.8.8.8") == "US"
    assert lookup.country("10.0.0.1") is None
    assert lookup.country("not-an-ip") is None
