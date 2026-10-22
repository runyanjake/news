"""Report history: one JSON file per local date, kept forever."""

import os
import re
from pathlib import Path

from .models import Report

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReportStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, date: str) -> Path:
        if not _DATE.match(date):
            raise ValueError(f"Invalid report date {date!r}")
        return self.directory / f"{date}.json"

    def save(self, report: Report) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(report.date)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(report.model_dump_json(indent=2))
        os.replace(tmp, path)

    def get(self, date: str) -> Report | None:
        path = self._path(date)
        if not path.exists():
            return None
        return Report.model_validate_json(path.read_text())

    def dates(self) -> list[str]:
        """All stored dates, newest first."""
        if not self.directory.exists():
            return []
        return sorted((p.stem for p in self.directory.glob("*.json") if _DATE.match(p.stem)), reverse=True)

    def latest(self) -> Report | None:
        dates = self.dates()
        return self.get(dates[0]) if dates else None
