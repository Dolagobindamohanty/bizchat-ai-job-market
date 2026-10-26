"""Write a validated CSV snapshot from API adapters."""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from collector.validate import validate

SNAPSHOT_COLUMNS = ["job_id", "source_job_id", "title", "company", "location", "description", "posted_date", "collected_at", "salary", "salary_is_predicted", "remote", "remote_type", "salary_min_lpa", "salary_max_lpa", "source", "source_url"]


def filter_recent_rows(rows: Iterable[dict], max_age_days: int | None, *, today: date | None = None) -> list[dict]:
    """Keep only dated postings within the publication freshness window."""
    if max_age_days is None:
        return [dict(row) for row in rows]
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    reference = today or date.today()
    cutoff = reference - timedelta(days=max_age_days)
    recent = []
    for row in rows:
        value = str(row.get("posted_date") or "").strip()[:10]
        try:
            posted = date.fromisoformat(value)
        except ValueError:
            continue
        if cutoff <= posted <= reference:
            recent.append(dict(row))
    return recent


def _collected_at(row: dict) -> datetime:
    value = str(row.get("collected_at") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min


def write_snapshot(rows: Iterable[dict], path: Path, *, allow_empty: bool = False) -> int:
    unique: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("source", "")).strip(), str(row.get("source_job_id") or row.get("job_id") or "").strip())
        if all(key) and (key not in unique or _collected_at(row) >= _collected_at(unique[key])):
            unique[key] = dict(row)
    if not unique and not allow_empty:
        raise ValueError("refusing to publish an empty job snapshot")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows({column: row.get(column, "") for column in SNAPSHOT_COLUMNS} for row in unique.values())
        validate(temporary)
        os.replace(temporary, path)
    finally:
      temporary.unlink(missing_ok=True)
    return len(unique)
