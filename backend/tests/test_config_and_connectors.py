import pytest

from app.config import load_config
from app.connectors import build_connector
from app.connectors.rss import RSSConnector
from app.models import SourceConfig
from app.settings import load_settings

# The deployed config: config/sources.yaml in a checkout, or SOURCES_FILE inside the image.
REPO_CONFIG = load_settings().sources_file

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
<item><title>Storm &amp; floods hit coast</title><link>https://x.example/1</link>
  <description>&lt;p&gt;Heavy <b>rain</b> fell.&lt;/p&gt;</description>
  <pubDate>Wed, 22 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title></title><link>https://x.example/2</link></item>
<item><title>No date</title><link>https://x.example/3</link></item>
</channel></rss>"""


def test_repo_config_is_valid():
    config = load_config(REPO_CONFIG)
    assert config.sources
    assert len({s.id for s in config.sources}) == len(config.sources)
    for source in config.sources:
        assert source.tags
        build_connector(source)  # every configured type must be registered


def test_env_vars_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("FEED_TOKEN", "s3cret")
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n  - id: a\n    name: A\n    type: rss\n    url: https://a.example/?k=${FEED_TOKEN}\n"
    )
    assert load_config(path).sources[0].url == "https://a.example/?k=s3cret"


def test_unknown_connector_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown connector type"):
        build_connector(SourceConfig(id="x", name="X", type="carrier-pigeon"))


def test_rss_parse_normalises_entries():
    source = SourceConfig(id="x", name="X News", type="rss", url="https://x.example/rss", reputation=0.7)
    articles = RSSConnector(source).parse(RSS)

    assert [a.title for a in articles] == ["Storm & floods hit coast", "No date"]
    first = articles[0]
    assert first.summary == "Heavy rain fell."
    assert first.published_at.isoformat() == "2026-10-22T12:00:00+00:00"
    assert (first.source_id, first.source_name, first.reputation) == ("x", "X News", 0.7)
    assert articles[1].published_at is None
