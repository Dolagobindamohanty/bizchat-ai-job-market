"""Repository regression and STATIC SQL checks (not live Postgres/RLS tests)."""

from pathlib import Path

from database.repository import DatabaseRepository


def test_save_commits_before_connection_closes():
    events = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append("cursor_closed")

        def execute(self, sql, params):
            assert "INSERT INTO saved_user_data" in sql
            assert params[1] == "verified-user"
            events.append("insert")

    class Connection:
        def __enter__(self):
            self.closed = False
            return self

        def __exit__(self, *args):
            self.closed = True
            events.append("connection_closed")

        def cursor(self):
            return Cursor()

        def commit(self):
            assert not self.closed
            events.append("commit")

    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo._connection_factory = Connection
    saved = repo.save_user_data("verified-user", "profile", {"role": "Engineer"})
    assert saved["user_id"] == "verified-user"
    assert events == ["insert", "cursor_closed", "commit", "connection_closed"]


def test_supabase_rls_migration_static_policy_checks():
    """STATIC: inspect migration text; does not establish live RLS enforcement."""
    sql = (Path(__file__).resolve().parents[1] / "database" / "supabase-rls.sql").read_text()
    normalized = " ".join(sql.lower().split())
    for table in ("saved_user_data", "jobs", "skills", "job_skills", "job_runs"):
        assert f"alter table public.{table} enable row level security;" in normalized
    assert "using (user_id = auth.uid()::text)" in normalized
    assert "with check (user_id = auth.uid()::text)" in normalized
    assert "for all to authenticated" in normalized
    assert "grant select, insert, update, delete on table public.saved_user_data to authenticated" in normalized
    assert "alter column id set default gen_random_uuid()" in normalized
    assert "alter table public.job_runs alter column id set default gen_random_uuid()" in normalized
    assert "alter column started_at set default now()" in normalized
    for column in ("created_at", "updated_at"):
        assert f"alter column {column} set default now()" in normalized
    assert "drop policy %i on public.%i" in normalized
    assert normalized.count("create policy ") == 1
    assert "from public, anon, authenticated" in normalized
