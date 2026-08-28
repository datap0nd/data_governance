from pathlib import Path

import app.database as database
from app.database import get_db, init_db
from app.scanner import pg_cron
from app.source_identity import upsert_postgres_identity


def _source(db, source_id: int, name: str, schedule: str) -> None:
    db.execute(
        """INSERT INTO sources
               (id, name, type, connection_info, discovered_by, archived,
                refresh_schedule)
           VALUES (?, ?, 'postgresql', ?, 'test', 0, ?)""",
        (source_id, name, name, schedule),
    )


def _identity(db, source_id: int, *, server: str, database_name: str) -> None:
    result = upsert_postgres_identity(
        db,
        source_id=source_id,
        server=server,
        database=database_name,
        schema="sales",
        relation="daily_totals",
        relation_kind="materialized_view",
        verified_at="2026-08-28T09:00:00+00:00",
    )
    assert result["status"] in {"claimed", "refreshed"}


class _CronCursor:
    def __init__(self, jobs=None):
        self.query = ""
        self.jobs = jobs

    def execute(self, query):
        self.query = str(query)

    def fetchone(self):
        if "information_schema.tables" in self.query:
            return (1,)
        return None

    def fetchall(self):
        if "FROM cron.job ORDER BY" in self.query:
            return self.jobs if self.jobs is not None else [
                (
                    17,
                    "15 6 * * *",
                    'REFRESH MATERIALIZED VIEW "sales"."daily_totals"',
                    "staging",
                    "cron_user",
                    True,
                )
            ]
        if "FROM cron.job_run_details" in self.query:
            return []
        return []


class _CronConnection:
    def __init__(self, jobs=None):
        self.closed = False
        self.jobs = jobs

    def cursor(self):
        return _CronCursor(self.jobs)

    def close(self):
        self.closed = True


def test_schedule_matches_exact_server_database_schema_and_mv(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "pg-cron-identity.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    init_db()

    connection = _CronConnection()
    monkeypatch.setattr(pg_cron, "PGHOST", "primary.internal")
    monkeypatch.setattr(pg_cron, "PGPORT", 5432)
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: connection)

    with get_db() as db:
        _source(db, 1, "warehouse sales.daily_totals", "warehouse-original")
        _source(db, 2, "staging sales.daily_totals", "staging-original")
        _source(db, 3, "other server staging sales.daily_totals", "other-original")
        _identity(db, 1, server="primary.internal", database_name="warehouse")
        _identity(db, 2, server="primary.internal", database_name="staging")
        _identity(db, 3, server="other.internal", database_name="staging")

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "completed"
    assert result["matched"] == 1
    assert connection.closed is True
    with get_db() as db:
        schedules = {
            row["id"]: row["refresh_schedule"]
            for row in db.execute(
                "SELECT id, refresh_schedule FROM sources ORDER BY id"
            ).fetchall()
        }
    assert schedules == {
        1: "warehouse-original",
        2: "15 6 * * *",
        3: "other-original",
    }


def test_command_parser_requires_one_live_schema_qualified_target():
    assert pg_cron._parse_mv_from_command(
        "REFRESH MATERIALIZED VIEW daily_totals"
    ) is None
    assert pg_cron._parse_mv_from_command(
        "-- REFRESH MATERIALIZED VIEW sales.old_mv\n"
        "REFRESH MATERIALIZED VIEW sales.live_mv"
    ) == ("sales", "live_mv")
    assert pg_cron._parse_mv_from_command(
        "REFRESH MATERIALIZED VIEW sales.first_mv; "
        "REFRESH MATERIALIZED VIEW sales.second_mv"
    ) is None
    assert pg_cron._parse_mv_from_command(
        "SELECT 'REFRESH MATERIALIZED VIEW sales.fake_mv'"
    ) is None
    assert pg_cron._parse_mv_from_command(
        'REFRESH MATERIALIZED VIEW sales."daily-totals"'
    ) == ("sales", "daily-totals")
    assert pg_cron._parse_mv_from_command(
        'REFRESH MATERIALIZED VIEW "Sales Ops"."Daily Totals" WITH NO DATA;'
    ) == ("Sales Ops", "Daily Totals")
    assert pg_cron._parse_mv_from_command(
        'REFRESH MATERIALIZED VIEW sales."daily""totals"'
    ) == ("sales", 'daily"totals')
    assert pg_cron._parse_mv_from_command(
        'REFRESH MATERIALIZED VIEW database.sales.daily_totals'
    ) is None


def test_unqualified_cron_refresh_does_not_update_public_source(
    tmp_path: Path,
    monkeypatch,
):
    db_path = str(tmp_path / "pg-cron-unqualified.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    init_db()
    jobs = [
        (
            18,
            "30 7 * * *",
            "REFRESH MATERIALIZED VIEW daily_totals",
            "staging",
            "cron_user",
            True,
        )
    ]
    connection = _CronConnection(jobs)
    monkeypatch.setattr(pg_cron, "PGHOST", "primary.internal")
    monkeypatch.setattr(pg_cron, "PGPORT", 5432)
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: connection)

    with get_db() as db:
        _source(db, 1, "public.daily_totals", "original")
        result = upsert_postgres_identity(
            db,
            source_id=1,
            server="primary.internal",
            database="staging",
            schema="public",
            relation="daily_totals",
            relation_kind="materialized_view",
            verified_at="2026-08-28T09:00:00+00:00",
        )
        assert result["status"] in {"claimed", "refreshed"}

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "completed"
    assert result["mv_jobs"] == 0
    assert result["matched"] == 0
    with get_db() as db:
        schedule = db.execute(
            "SELECT refresh_schedule FROM sources WHERE id=1"
        ).fetchone()[0]
    assert schedule == "original"
