from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id UUID PRIMARY KEY,
    run_date DATE NOT NULL,
    status TEXT NOT NULL,
    jobs_seen INTEGER NOT NULL DEFAULT 0,
    jobs_saved INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS job_runs_run_date_idx ON job_runs(run_date DESC);
CREATE TABLE IF NOT EXISTS saved_user_data (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    data_type TEXT NOT NULL CHECK (data_type IN ('profile', 'search', 'learning_plan')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
-- Keep one current document per owner and document type. The cleanup makes
-- this safe to apply to installations created before the unique index existed.
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY user_id, data_type
        ORDER BY updated_at DESC, created_at DESC, id DESC
    ) AS row_number
    FROM saved_user_data
)
DELETE FROM saved_user_data AS saved
USING ranked
WHERE saved.id = ranked.id AND ranked.row_number > 1;
CREATE UNIQUE INDEX IF NOT EXISTS saved_user_data_user_type_uidx
    ON saved_user_data(user_id, data_type);
CREATE INDEX IF NOT EXISTS saved_user_data_user_type_idx ON saved_user_data(user_id, data_type, updated_at DESC);
"""


class DatabaseRepository:
    """Small persistence boundary with a dependency-free local mode.

    Set DATABASE_URL (or POSTGRES_URL) to enable PostgreSQL. Without it, data
    is held in memory so CSV-only development and tests require no service.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
        self._lock = threading.RLock()
        self._runs: list[dict[str, Any]] = []
        self._saved: list[dict[str, Any]] = []
        self._connection_factory = None
        if self.database_url:
            try:
                import psycopg
                self._connection_factory = lambda: psycopg.connect(self.database_url)
                self._ensure_schema()
            except ImportError as exc:
                raise RuntimeError("DATABASE_URL is set but psycopg is not installed") from exc

    @property
    def postgres_enabled(self) -> bool:
        return self._connection_factory is not None

    def _ensure_schema(self) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
            connection.commit()

    def record_job_run(self, *, run_date: str, status: str, jobs_seen: int = 0,
                       jobs_saved: int = 0, source: str | None = None,
                       error: str | None = None, started_at: datetime | None = None,
                       finished_at: datetime | None = None) -> dict[str, Any]:
        record = {"id": str(uuid.uuid4()), "run_date": run_date, "status": status,
                  "jobs_seen": jobs_seen, "jobs_saved": jobs_saved, "source": source,
                  "error": error, "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
                  "finished_at": finished_at.isoformat() if finished_at else None}
        if self.postgres_enabled:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""INSERT INTO job_runs (id, run_date, status, jobs_seen, jobs_saved, source, error, started_at, finished_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (record["id"], run_date, status, jobs_seen, jobs_saved, source, error, record["started_at"], record["finished_at"]))
                connection.commit()
        else:
            with self._lock:
                self._runs.append(record)
        return record

    def list_job_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        if self.postgres_enabled:
            with self._connection_factory() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT id, run_date, status, jobs_seen, jobs_saved, source, error, started_at, finished_at FROM job_runs ORDER BY run_date DESC, started_at DESC LIMIT %s", (limit,))
                columns = [column.name for column in cursor.description]
                return [self._json_dates(dict(zip(columns, row))) for row in cursor.fetchall()]
        with self._lock:
            return sorted(self._runs, key=lambda item: (item["run_date"], item["started_at"], item["id"]), reverse=True)[:limit]

    def save_user_data(self, user_id: str, data_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if data_type not in {"profile", "search", "learning_plan"}:
            raise ValueError("unsupported saved data type")
        if len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 20 * 1024:
            raise ValueError("saved payload exceeds the 20KB limit")
        now = datetime.now(timezone.utc).isoformat()
        record = {"id": str(uuid.uuid4()), "user_id": user_id, "data_type": data_type,
                  "payload": payload, "created_at": now, "updated_at": now}
        if self.postgres_enabled:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""INSERT INTO saved_user_data
                        (id, user_id, data_type, payload, created_at, updated_at)
                        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                        ON CONFLICT (user_id, data_type) DO UPDATE SET
                            payload = EXCLUDED.payload,
                            updated_at = EXCLUDED.updated_at
                        RETURNING id, user_id, data_type, payload, created_at, updated_at""",
                                   (record["id"], user_id, data_type, json.dumps(payload), now, now))
                    # Keep compatibility with lightweight repository test
                    # doubles that only model execute/commit.
                    if hasattr(cursor, "fetchone"):
                        row = cursor.fetchone()
                        if row is not None:
                            record = dict(zip(("id", "user_id", "data_type", "payload", "created_at", "updated_at"), row))
                            record = self._json_dates(record)
                connection.commit()
        else:
            with self._lock:
                existing = next((item for item in self._saved
                                 if item["user_id"] == user_id and item["data_type"] == data_type), None)
                if existing is None:
                    self._saved.append(record)
                else:
                    existing.update(payload=payload, updated_at=now)
                    record = existing
        return record

    def list_user_data(self, user_id: str, data_type: str) -> list[dict[str, Any]]:
        if self.postgres_enabled:
            with self._connection_factory() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT id, user_id, data_type, payload, created_at, updated_at FROM saved_user_data WHERE user_id = %s AND data_type = %s ORDER BY updated_at DESC", (user_id, data_type))
                columns = [column.name for column in cursor.description]
                return [self._json_dates(dict(zip(columns, row))) for row in cursor.fetchall()]
        with self._lock:
            return [item for item in reversed(self._saved) if item["user_id"] == user_id and item["data_type"] == data_type]

    @staticmethod
    def _json_dates(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value.isoformat() if isinstance(value, (datetime,)) or hasattr(value, "isoformat") and key.endswith(("_at", "_date")) else value for key, value in record.items()}


_repository = DatabaseRepository()


def get_repository() -> DatabaseRepository:
    return _repository
