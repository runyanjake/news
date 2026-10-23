"""Report history, kept forever.

    <dir>/<date>.json                       newest generation of each local date
    <dir>/generations/<date>/<stamp>.json   every generation, stamp = UTC generated_at (YYYYMMDDTHHMMSSZ)
"""

import os
import re
from datetime import UTC
from pathlib import Path

from .models import Report

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP = re.compile(r"^\d{8}T\d{6}Z$")


def generation_stamp(report: Report) -> str:
    return report.generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _check(pattern: re.Pattern, value: str, what: str) -> str:
    if not pattern.match(value):
        raise ValueError(f"Invalid report {what} {value!r}")
    return value


def _write(path: Path, report: Report) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(report.model_dump_json(indent=2))
    os.replace(tmp, path)


def _read(path: Path) -> Report | None:
    return Report.model_validate_json(path.read_text()) if path.exists() else None


class ReportStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, date: str) -> Path:
        return self.directory / f"{_check(_DATE, date, 'date')}.json"

    def _generations_dir(self, date: str) -> Path:
        return self.directory / "generations" / _check(_DATE, date, "date")

    def save(self, report: Report) -> None:
        """Keep the generation in history and make it the current report for its date."""
        _write(self._generations_dir(report.date) / f"{generation_stamp(report)}.json", report)
        current = self.get(report.date)
        if current is None or current.generated_at <= report.generated_at:
            _write(self._path(report.date), report)

    def get(self, date: str) -> Report | None:
        return _read(self._path(date))

    def dates(self) -> list[str]:
        """All stored dates, newest first."""
        if not self.directory.exists():
            return []
        return sorted((p.stem for p in self.directory.glob("*.json") if _DATE.match(p.stem)), reverse=True)

    def generations(self, date: str) -> list[str]:
        """Generation stamps stored for a date, newest first."""
        directory = self._generations_dir(date)
        if not directory.exists():
            return []
        return sorted((p.stem for p in directory.glob("*.json") if _STAMP.match(p.stem)), reverse=True)

    def get_generation(self, date: str, stamp: str) -> Report | None:
        return _read(self._generations_dir(date) / f"{_check(_STAMP, stamp, 'generation')}.json")

    def latest(self) -> Report | None:
        dates = self.dates()
        return self.get(dates[0]) if dates else None
