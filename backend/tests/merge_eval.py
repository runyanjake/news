"""Labeled same-story pairs for measuring the merge step, offline and repeatably.

Fixture files live in tests/data/merge/ (read-only to the test suite):

    <day>.labels.yaml        hand-written: pairs of headlines labeled same / related / different
    <day>.json               built by `snapshot`: the labeled articles (title + the summary excerpt
                             the pipeline compares) and the rest of that day as unordered word bags,
                             so TF-IDF weights match production without storing the other articles
    embeddings/<model>.json  optional cache written by `eval --embeddings`, so tests run offline

"related" pairs (follow-ups, analysis of the same event) are reported but never fail anything:
whether they belong in one report entry is an editorial choice.

    cd backend
    python -m tests.merge_eval snapshot FEEDS_DIR tests/data/merge/2026-11-03.labels.yaml
    python -m tests.merge_eval eval tests/data/merge/2026-11-03.json
    python -m tests.merge_eval eval tests/data/merge/2026-11-03.json --embeddings nomic-embed-text
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.config import load_config
from app.connectors import build_connector
from app.models import Article
from app.pipeline.grouping import story_match
from app.pipeline.preprocess import SUMMARY_CHARS, annotate, embedding_text, preprocess, weigh_terms
from app.pipeline.text import tokens, unit

DATA = Path(__file__).parent / "data" / "merge"
REPO_CONFIG = Path(__file__).parents[2] / "config" / "sources.yaml"
LABELS = ("same", "related", "different")


@dataclass
class Pair:
    a: Article
    b: Article
    label: str
    note: str = ""


@dataclass
class Score:
    threshold: float
    merged: dict[str, int]  # label -> pairs the merge step would merge
    totals: dict[str, int]
    misses: list[Pair]  # wrong decisions on same/different pairs

    @property
    def recall(self) -> float:
        return self.merged["same"] / self.totals["same"] if self.totals["same"] else 1.0

    @property
    def false_merges(self) -> int:
        return self.merged["different"]


def load(path: Path) -> tuple[list[Article], list[Pair]]:
    """Labeled articles, weighted against the full day exactly as preprocess weighs them."""
    data = json.loads(Path(path).read_text())
    labeled = {
        row["id"]: annotate(Article(source_id=row["source_id"], source_name=row["source_name"],
                                    title=row["title"], summary=row["summary"], url=row["id"]))
        for row in data["articles"]
    }
    background = [
        annotate(Article(source_id=row["source_id"], source_name=row["source_id"], title="",
                         summary=" ".join(row["words"]), url=f"background:{i}"))
        for i, row in enumerate(data["background"])
    ]
    weigh_terms([*labeled.values(), *background])
    pairs = [Pair(labeled[p["a"]], labeled[p["b"]], p["label"], p.get("note", "")) for p in data["pairs"]]
    return list(labeled.values()), pairs


def evaluate(pairs: list[Pair], threshold: float, embedding_threshold: float | None = None) -> Score:
    merged = dict.fromkeys(LABELS, 0)
    totals = dict.fromkeys(LABELS, 0)
    misses = []
    for pair in pairs:
        same = story_match([pair.a], [pair.b], threshold, embedding_threshold) is not None
        totals[pair.label] += 1
        merged[pair.label] += same
        if pair.label != "related" and same != (pair.label == "same"):
            misses.append(pair)
    return Score(embedding_threshold if embedding_threshold is not None else threshold, merged, totals, misses)


def attach_embeddings(articles: list[Article], model: str, fetch: bool) -> bool:
    """Use cached embeddings for `model`, fetching (and caching) them from the LLM server if asked."""
    cache = DATA / "embeddings" / f"{model.replace('/', '_')}.json"
    vectors = json.loads(cache.read_text())["vectors"] if cache.exists() else {}
    missing = [a for a in articles if a.url not in vectors]
    if missing and fetch:
        from app.llm import LLMClient
        from app.settings import load_settings

        settings = load_settings()
        llm = LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout)
        fetched, _ = asyncio.run(llm.embed([embedding_text(a) for a in missing], model))
        vectors.update({a.url: v for a, v in zip(missing, fetched, strict=True)})
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"model": model, "vectors": vectors}))
        missing = []
    if missing:
        return False
    for article in articles:
        article.embedding = unit(vectors[article.url])
    return True


def snapshot(feeds_dir: Path, labels_path: Path, now: datetime | None = None) -> Path:
    """Build <day>.json from saved feeds (<source id>.xml) and a labels file."""
    config = load_config(REPO_CONFIG)
    per_source = []
    for source in config.sources:
        feed = Path(feeds_dir) / f"{source.id}.xml"
        if source.enabled and feed.exists():
            per_source.append(build_connector(source).parse(feed.read_bytes()))
    now = now or datetime.fromtimestamp(max(os.path.getmtime(f) for f in Path(feeds_dir).glob("*.xml")), UTC)
    articles = preprocess(per_source, config.pipeline, now)

    def find(spec: str | dict) -> Article:
        """A headline fragment, or {source: <source id>, title: <fragment>} when it is ambiguous."""
        source, text = (spec["source"], spec["title"]) if isinstance(spec, dict) else (None, spec)
        hits = [a for a in articles if text.lower() in a.title.lower() and source in (None, a.source_id)]
        if len(hits) != 1:
            raise SystemExit(f"{spec!r} matches {len(hits)} articles; quote more of the headline or give a source")
        return hits[0]

    labels = yaml.safe_load(Path(labels_path).read_text())
    problems = []
    for entry in labels["pairs"]:
        if entry["label"] not in LABELS:
            problems.append(f"Unknown label {entry['label']!r}; use one of {LABELS}")
        for spec in (entry["a"], entry["b"]):
            try:
                find(spec)
            except SystemExit as exc:
                problems.append(str(exc))
    if problems:
        raise SystemExit("\n".join(problems))
    rows, pairs = {}, []
    for entry in labels["pairs"]:
        a, b = find(entry["a"]), find(entry["b"])
        for article in (a, b):
            rows[article.url_key] = {
                "id": article.url_key, "source_id": article.source_id, "source_name": article.source_name,
                "title": article.title, "summary": article.summary[:SUMMARY_CHARS],
            }
        pairs.append({"a": a.url_key, "b": b.url_key, "label": entry["label"], "note": entry.get("note", "")})
    background = [
        {"source_id": a.source_id, "words": sorted(a.words | tokens(a.summary[:SUMMARY_CHARS]))}
        for a in articles if a.url_key not in rows
    ]
    out = Path(labels_path).with_name(Path(labels_path).name.replace(".labels.yaml", ".json"))
    out.write_text(json.dumps({"articles": list(rows.values()), "background": background, "pairs": pairs},
                              indent=1, ensure_ascii=False) + "\n")
    return out


def _report(name: str, scores: list[Score], chosen: float) -> None:
    print(f"\n{name}: threshold | same merged (recall) | related merged | different merged (false merges)")
    for s in scores:
        mark = "  <- configured" if abs(s.threshold - chosen) < 1e-9 else ""
        print(f"  {s.threshold:5.3f} | {s.merged['same']:3}/{s.totals['same']:<3} ({s.recall:4.0%}) "
              f"| {s.merged['related']:3}/{s.totals['related']:<3} | {s.merged['different']:3}/{s.totals['different']:<3}{mark}")
    best = next((s for s in scores if abs(s.threshold - chosen) < 1e-9), None)
    for pair in best.misses if best else []:
        verdict = "missed" if pair.label == "same" else "false merge"
        print(f"    {verdict}: [{pair.a.source_name}] {pair.a.title}  ~  [{pair.b.source_name}] {pair.b.title}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m tests.merge_eval", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="build a fixture from saved feeds and a labels file")
    snap.add_argument("feeds_dir", type=Path)
    snap.add_argument("labels", type=Path)
    ev = sub.add_parser("eval", help="score labeled pairs across thresholds")
    ev.add_argument("fixture", type=Path)
    ev.add_argument("--embeddings", metavar="MODEL", help="also score embeddings (fetched from LLM_BASE_URL once, then cached)")
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        print(f"Wrote {snapshot(args.feeds_dir, args.labels)}")
        return
    grouping = load_config(REPO_CONFIG).grouping
    articles, pairs = load(args.fixture)
    print(f"{args.fixture.name}: {len(pairs)} labeled pairs over {len(articles)} articles")
    sweep = [round(0.10 + 0.025 * i, 3) for i in range(9)]
    _report("tf-idf", [evaluate(pairs, t) for t in sweep], grouping.merge_threshold)
    if args.embeddings:
        if not attach_embeddings(articles, args.embeddings, fetch=True):
            raise SystemExit("No embeddings available")
        chosen = grouping.embeddings.merge_threshold if grouping.embeddings else 0.8
        _report(f"embeddings ({args.embeddings})",
                [evaluate(pairs, grouping.merge_threshold, round(0.5 + 0.05 * i, 2)) for i in range(10)], chosen)


if __name__ == "__main__":
    main(sys.argv[1:])
