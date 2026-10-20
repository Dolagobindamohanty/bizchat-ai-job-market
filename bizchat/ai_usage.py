"""Atomic hosted-call reservations; the ledger stores UTC days and counts only.

Workers must share this SQLite file for a shared allowance. Persistence across
deployments requires a shared durable disk; the default temporary location does
not promise that. Failed external requests deliberately retain their reservation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import getpass
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import tempfile


DEFAULT_DAILY_CALL_LIMIT = 100


class UsageError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _database_path() -> Path:
    configured = os.getenv("JOBMATCH_USAGE_DB", "").strip()
    if configured:
        return Path(configured)
    # Private per-user runtime directory, outside the source tree. Do not change
    # permissions on an operator-supplied database or create its parent folders.
    user = sha256(getpass.getuser().encode()).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / ("jobmatch-ai-" + user)
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory / "usage.sqlite3"


def reserve_call() -> None:
    """Reserve one global call atomically, failing closed on any ledger failure.

    Zero disables hosted calls. No provider key, identity, prompt, profile, job,
    URL, or response is accepted by this interface or stored in the ledger.
    """
    try:
        limit = int(os.getenv("JOBMATCH_AI_DAILY_CALL_LIMIT", str(DEFAULT_DAILY_CALL_LIMIT)))
        if limit < 0:
            raise ValueError()
    except ValueError:
        raise UsageError("Hosted AI usage controls are unavailable.", 503) from None
    if limit == 0:
        raise UsageError("Hosted AI daily call limit reached. Please try again after the next UTC day.", 429)
    try:
        path = _database_path()
        # Each reservation uses its own connection, so this also serializes
        # concurrent threads and processes sharing the same database file.
        connection = sqlite3.connect(str(path), timeout=5.0)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                day = _utc_day()
                connection.execute("CREATE TABLE IF NOT EXISTS daily_usage (day TEXT NOT NULL, category TEXT NOT NULL CHECK(category = 'global'), calls INTEGER NOT NULL CHECK(calls >= 0), PRIMARY KEY(day, category))")
                row = connection.execute("SELECT calls FROM daily_usage WHERE day = ? AND category = 'global'", (day,)).fetchone()
                if row and row[0] >= limit:
                    raise UsageError("Hosted AI daily call limit reached. Please try again after the next UTC day.", 429)
                connection.execute("INSERT INTO daily_usage(day, category, calls) VALUES (?, 'global', 1) ON CONFLICT(day, category) DO UPDATE SET calls = calls + 1", (day,))
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError):
        raise UsageError("Hosted AI usage controls are unavailable. Please try again later.", 503) from None
