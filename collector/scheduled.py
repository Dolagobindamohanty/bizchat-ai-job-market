"""Integration points for an external scheduler or permitted collector."""

from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

from database.repository import DatabaseRepository


def run_scheduled_collection(
    collector: Callable[[], Iterable[dict[str, Any]]],
    repository: DatabaseRepository,
    *, source: str,
    run_date: date | None = None,
) -> dict[str, Any]:
    """Run a supplied (already-authorized) collector and persist run history.

    The API never invokes collectors during requests; cron, GitHub Actions, or
    a worker can call this integration point with its own collector function.
    """
    started = datetime.now(timezone.utc)
    try:
        rows = list(collector())
        record = repository.record_job_run(run_date=(run_date or date.today()).isoformat(), status="success",
                                           jobs_seen=len(rows), jobs_saved=len(rows), source=source,
                                           started_at=started, finished_at=datetime.now(timezone.utc))
        return record
    except Exception as exc:
        return repository.record_job_run(run_date=(run_date or date.today()).isoformat(), status="failed",
                                         source=source, error=str(exc), started_at=started,
                                         finished_at=datetime.now(timezone.utc))
