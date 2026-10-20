from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
import sqlite3

import pytest

from bizchat import ai_usage as usage


@pytest.fixture(autouse=True)
def isolated_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBMATCH_USAGE_DB", str(tmp_path / "usage.sqlite3"))
    monkeypatch.delenv("JOBMATCH_AI_DAILY_CALL_LIMIT", raising=False)


def test_default_limit_is_100_and_persists_across_connections(tmp_path):
    for _ in range(100):
        usage.reserve_call()
    with pytest.raises(usage.UsageError) as error:
        usage.reserve_call()
    assert error.value.status_code == 429
    with sqlite3.connect(tmp_path / "usage.sqlite3") as connection:
        assert connection.execute("SELECT calls FROM daily_usage").fetchall() == [(100,)]


def test_utc_day_rollover_has_new_allowance(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBMATCH_AI_DAILY_CALL_LIMIT", "1")
    monkeypatch.setattr(usage, "_utc_day", lambda: "2026-10-20")
    usage.reserve_call()
    with pytest.raises(usage.UsageError):
        usage.reserve_call()
    monkeypatch.setattr(usage, "_utc_day", lambda: "2026-10-21")
    usage.reserve_call()
    with sqlite3.connect(tmp_path / "usage.sqlite3") as connection:
        assert connection.execute("SELECT day, category, calls FROM daily_usage ORDER BY day").fetchall() == [
            ("2026-10-20", "global", 1), ("2026-10-21", "global", 1)]


def test_concurrent_connections_cannot_exceed_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBMATCH_AI_DAILY_CALL_LIMIT", "7")
    def attempt(_):
        try:
            usage.reserve_call()
            return 200
        except usage.UsageError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(attempt, range(40)))
    assert statuses.count(200) == 7
    assert statuses.count(429) == 33
    with sqlite3.connect(tmp_path / "usage.sqlite3") as connection:
        assert connection.execute("SELECT calls FROM daily_usage").fetchall() == [(7,)]


def _process_reservation(_):
    try:
        usage.reserve_call()
        return 200
    except usage.UsageError as error:
        return error.status_code


def test_workers_share_one_atomic_allowance(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBMATCH_AI_DAILY_CALL_LIMIT", "3")
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        statuses = list(pool.map(_process_reservation, range(16)))
    assert statuses.count(200) == 3
    assert statuses.count(429) == 13
    with sqlite3.connect(tmp_path / "usage.sqlite3") as connection:
        assert connection.execute("SELECT calls FROM daily_usage").fetchall() == [(3,)]


@pytest.mark.parametrize("limit,status", [("0", 429), ("-1", 503), ("bad", 503), ("1.5", 503), ("", 503)])
def test_zero_disables_calls_and_invalid_limits_fail_closed(monkeypatch, tmp_path, limit, status):
    monkeypatch.setenv("JOBMATCH_AI_DAILY_CALL_LIMIT", limit)
    with pytest.raises(usage.UsageError) as error:
        usage.reserve_call()
    assert error.value.status_code == status
    assert not (tmp_path / "usage.sqlite3").exists()


def test_corrupt_database_fails_closed_without_leaking_details(tmp_path):
    (tmp_path / "usage.sqlite3").write_text("private-corrupt-data")
    with pytest.raises(usage.UsageError) as error:
        usage.reserve_call()
    assert error.value.status_code == 503
    assert "private" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_default_ledger_is_in_private_runtime_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("JOBMATCH_USAGE_DB")
    monkeypatch.setattr(usage.tempfile, "gettempdir", lambda: str(tmp_path))
    path = usage._database_path()
    assert path.parent.parent == tmp_path
    assert path.parent.name.startswith("jobmatch-ai-")
    usage.reserve_call()
    assert path.is_file()
