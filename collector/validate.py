"""Placeholder CSV validation/normalization command.

This module deliberately performs no network access. A future permitted-source
adapter can write the same contract before this check is run.
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import dataclass

REQUIRED_COLUMNS = {
    "job_id", "title", "source", "source_url", "posted_date", "collected_at"
}


@dataclass(frozen=True)
class ValidationReport:
    """Machine-readable results for the CSV contract checks."""

    dataset_row_count: int
    required_columns_present: bool
    duplicate_key_count: int
    missing_source_urls: int
    invalid_timestamps: int
    invalid_source_urls: int = 0
    invalid_keys: int = 0

    @property
    def deployment_safe(self) -> bool:
        return (
            self.required_columns_present
            and self.duplicate_key_count == 0
            and self.missing_source_urls == 0
            and self.invalid_source_urls == 0
            and self.invalid_keys == 0
            and self.invalid_timestamps == 0
        )


def validation_report(path: Path) -> ValidationReport:
    """Inspect a CSV without network access or raising on bad row data."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        if not REQUIRED_COLUMNS.issubset(columns):
            # There is no useful row-level validation when the contract itself
            # is incomplete, but still count rows for an actionable report.
            rows = sum(1 for _ in reader)
            return ValidationReport(rows, False, 0, 0, 0)

        seen: set[tuple[str, str]] = set()
        duplicates = 0
        missing_urls = 0
        invalid_urls = 0
        invalid_keys = 0
        invalid_timestamps = 0
        rows = 0
        for row in reader:
            rows += 1
            key = (row.get("source", "").strip(), row.get("job_id", "").strip())
            if not all(key):
                invalid_keys += 1
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)

            source_url = row.get("source_url", "").strip()
            if not source_url:
                missing_urls += 1
            else:
                parsed_url = urlparse(source_url)
                if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                    invalid_urls += 1

            try:
                timestamp = datetime.fromisoformat(row.get("collected_at", "").replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    invalid_timestamps += 1
            except (TypeError, ValueError):
                invalid_timestamps += 1

        return ValidationReport(rows, True, duplicates, missing_urls, invalid_timestamps, invalid_urls, invalid_keys)


def validate(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")

        seen: set[tuple[str, str]] = set()
        rows = 0
        for row_number, row in enumerate(reader, start=2):
            rows += 1
            key = (row["source"].strip(), row["job_id"].strip())
            if not all(key):
                raise ValueError(f"row {row_number}: source and job_id are required")
            if key in seen:
                raise ValueError(f"row {row_number}: duplicate source/job_id {key}")
            seen.add(key)

            parsed_url = urlparse(row["source_url"].strip())
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise ValueError(f"row {row_number}: source_url must be an HTTP(S) URL")
            try:
                timestamp = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"row {row_number}: collected_at must be ISO-8601") from exc
            if timestamp.tzinfo is None:
                raise ValueError(f"row {row_number}: collected_at must include a timezone")

    print(f"Validated {rows} rows from {path}; normalization is a dry-run placeholder.")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the local job CSV without network access")
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    validate(args.input)


if __name__ == "__main__":
    main()
