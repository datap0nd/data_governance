from pathlib import Path

import app.database as database
from app.database import get_db, init_db
from app.scanner import pg_cron
from app.source_identity import upsert_postgres_identity
from app.freshness_inheritance import upsert_schedule_evidence


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
        if "FROM pg_extension" in self.query:
            return (True, True, True, True, True)
        if "current_setting('cron.timezone'" in self.query:
            return (None, "UTC")
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


def _governed_mv(db, source_id: int = 10) -> None:
    _source(db, source_id, "sales.daily_totals", "")
    db.execute("INSERT INTO reports(id, name, archived) VALUES (1, 'Sales', 0)")
    db.execute(
        "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Sales', ?)",
        (source_id,),
    )
    _identity(db, source_id, server="primary.internal", database_name="staging")


class _CapabilityCursor(_CronCursor):
    def __init__(self, *, capability=(True, True, True, True, True), jobs=None, fail_stage=None):
        super().__init__(jobs)
        self.capability = capability
        self.fail_stage = fail_stage

    def execute(self, query):
        text = str(query)
        if self.fail_stage == "job" and "FROM cron.job ORDER BY" in text:
            raise RuntimeError("job catalog unavailable")
        if self.fail_stage == "history" and "FROM cron.job_run_details" in text:
            raise RuntimeError("history unavailable")
        super().execute(query)

    def fetchone(self):
        if "FROM pg_extension" in self.query:
            return self.capability
        return super().fetchone()


class _CursorConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.closed = False

    def cursor(self):
        return self.cursor_value

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


def test_missing_credentials_are_contextual_warning_for_governed_mv(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "missing-creds.db"))
    init_db()
    monkeypatch.setattr(pg_cron, "PGHOST", "")
    monkeypatch.setattr(pg_cron, "PGUSER", "")
    monkeypatch.setattr(pg_cron, "PGPASSWORD", "")
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: None)
    with get_db() as db:
        _governed_mv(db)

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "completed_with_warnings"
    assert result["reason_code"] == "postgres_credentials_not_configured"
    assert result["diagnostic"]["facts"]["schedule_evidence_needed"] == 1


def test_connection_failure_is_not_reported_as_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "connection.db"))
    init_db()
    monkeypatch.setattr(pg_cron, "PGHOST", "primary.internal")
    monkeypatch.setattr(pg_cron, "PGUSER", "scanner")
    monkeypatch.setattr(pg_cron, "PGPASSWORD", "configured")
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: None)

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "skipped"
    assert result["reason_code"] == "postgres_connection_failed"


def test_pg_cron_absence_and_permission_are_distinct(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "capabilities.db"))
    init_db()
    monkeypatch.setattr(pg_cron, "PGHOST", "primary.internal")
    monkeypatch.setattr(pg_cron, "PGPORT", 5432)

    absent = _CursorConnection(_CapabilityCursor(capability=(False, False, False, False, False)))
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: absent)
    assert pg_cron.scan_pg_cron()["reason_code"] == "pg_cron_not_installed"

    denied = _CursorConnection(_CapabilityCursor(capability=(True, True, False, False, False)))
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: denied)
    assert pg_cron.scan_pg_cron()["reason_code"] == "pg_cron_permission_denied"


def test_job_query_failure_has_its_own_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "job-query.db"))
    init_db()
    connection = _CursorConnection(_CapabilityCursor(fail_stage="job"))
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: connection)

    result = pg_cron.scan_pg_cron()

    assert result["reason_code"] == "pg_cron_job_query_failed"
    assert "job catalog" in result["diagnostic"]["operator_summary"]


def test_run_history_failure_keeps_schedule_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "run-history.db"))
    init_db()
    connection = _CursorConnection(_CapabilityCursor(fail_stage="history"))
    monkeypatch.setattr(pg_cron, "PGHOST", "primary.internal")
    monkeypatch.setattr(pg_cron, "PGPORT", 5432)
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: connection)
    with get_db() as db:
        _governed_mv(db)

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "completed_with_warnings"
    assert result["reason_code"] == "pg_cron_run_history_unavailable"
    with get_db() as db:
        assert db.execute(
            "SELECT refresh_schedule FROM sources WHERE id=10"
        ).fetchone()["refresh_schedule"] == "15 6 * * *"


def test_empty_job_snapshot_retains_prior_trusted_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "empty-retain.db"))
    init_db()
    connection = _CursorConnection(_CapabilityCursor(jobs=[]))
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: connection)
    with get_db() as db:
        _source(db, 10, "sales.daily_totals", "15 6 * * *")
        upsert_schedule_evidence(
            db,
            source_id=10,
            origin="pg_cron",
            external_id="17",
            expression="15 6 * * *",
            timezone_name="UTC",
            active=True,
            authoritative=True,
            generation="trusted",
        )

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "completed_with_warnings"
    assert result["reason_code"] == "pg_cron_no_visible_jobs"
    assert result["prior_evidence_retained"] == 1
    with get_db() as db:
        assert db.execute(
            "SELECT active FROM source_schedule_evidence WHERE origin='pg_cron'"
        ).fetchone()["active"] == 1


def test_permission_filtered_partial_snapshot_retains_unseen_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "partial-retain.db"))
    init_db()
    visible_job = [(
        18,
        "30 7 * * *",
        "REFRESH MATERIALIZED VIEW sales.other_mv",
        "staging",
        "scanner",
        True,
    )]
    connection = _CursorConnection(_CapabilityCursor(
        capability=(True, True, True, True, False), jobs=visible_job
    ))
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: connection)
    with get_db() as db:
        _source(db, 10, "sales.daily_totals", "15 6 * * *")
        upsert_schedule_evidence(
            db,
            source_id=10,
            origin="pg_cron",
            external_id="17",
            expression="15 6 * * *",
            timezone_name="UTC",
            active=True,
            authoritative=True,
            generation="trusted",
        )

    result = pg_cron.scan_pg_cron()

    assert result["status"] == "completed_with_warnings"
    assert result["reason_code"] == "pg_cron_snapshot_incomplete"
    assert result["diagnostic"]["facts"]["prior_evidence_retained"] == 1
    with get_db() as db:
        assert db.execute(
            "SELECT active FROM source_schedule_evidence WHERE external_id='17'"
        ).fetchone()["active"] == 1
