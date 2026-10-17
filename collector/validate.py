"""Placeholder CSV validation/normalization command.

This module deliberately performs no network access. A future permitted-source
adapter can write the same contract before this check is run.
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

REQUIRED_COLUMNS = {
    "job_id", "title", "source", "source_url", "posted_date", "collected_at"
}


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
