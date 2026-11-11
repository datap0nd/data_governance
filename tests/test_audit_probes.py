"""Regression tests for the scanner/probe audit findings (S-04 to S-19, F-23).

Every test uses synthetic fixtures: a disposable SQLite database and fake
Power BI / PostgreSQL clients. Nothing here reaches a live system.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import database
from app.scanner import control, pbi_fetch, pbi_sync


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    control.clear_pbi_callback_fence()
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "governance.db"))
    database.init_db()
    yield
    control.clear_pbi_callback_fence()


def _iso(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


# ---------------------------------------------------------------------------
# S-04: "could not be read this time" is not "there is none"
# ---------------------------------------------------------------------------

DAILY = {
    "enabled": True,
    "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "times": ["06:00"],
}
DAILY_TEXT = "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday @ 06:00"
NO_SCHEDULE = {"enabled": False, "days": [], "times": [], "timezone": ""}


class _FakeHttpClient:
    def __init__(self, *, timeout, proxy):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _fake_refresh_api(monkeypatch, responses: dict):
    """Serve workspace REST calls from ``responses`` keyed by URL suffix."""
    monkeypatch.setattr(pbi_fetch, "get_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(pbi_fetch, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(pbi_fetch.httpx, "Client", _FakeHttpClient)
    monkeypatch.setattr(
        pbi_fetch,
        "_find_workspace",
        lambda _client, _token, _name: {"id": "ws-1", "name": "Governed Workspace"},
    )

    def fake_get_json(_client, _token, url, params=None):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"Unexpected Power BI call: {url}")

    monkeypatch.setattr(pbi_fetch, "_get_json", fake_get_json)


def _refresh_entry(report_name="Alpha", **overrides):
    entry = {
        "report_name": report_name,
        "dataset_id": "dataset-1",
        "web_url": "https://app.powerbi.test/reports/alpha",
        "schedule": dict(DAILY),
        "last_refresh": {"end_time": _iso(timedelta(hours=2)), "status": "Completed"},
    }
    entry.update(overrides)
    return entry


def _refresh_payload(*entries, **extra):
    return {
        "workspace": "Governed Workspace",
        "synced_at": _iso(),
        "reports": list(entries),
        **extra,
    }


def _seed_report(report_id=1, name="Alpha", archived=0, **fields):
    columns = ["id", "name", "archived", *fields]
    values = [report_id, name, archived, *fields.values()]
    with database.get_db() as db:
        db.execute(
            f"INSERT INTO reports ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in values)})",
            values,
        )


def _report_row(report_id=1) -> dict:
    with database.get_db() as db:
        return dict(db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone())


def _refresh_actions(report_id=1) -> list[tuple[str, str]]:
    with database.get_db() as db:
        rows = db.execute(
            """SELECT type, status FROM actions
               WHERE report_id = ? AND type IN ('refresh_failed', 'refresh_overdue')
               ORDER BY id""",
            (report_id,),
        ).fetchall()
    return [(row["type"], row["status"]) for row in rows]


def test_s04_fetch_flags_unreadable_schedule_and_history_separately(monkeypatch):
    throttled = pbi_fetch.PbiFetchError("Power BI API error 429")
    _fake_refresh_api(
        monkeypatch,
        {
            "/groups/ws-1/reports": {
                "value": [
                    {"name": "No Schedule Read", "datasetId": "d1", "webUrl": "u1"},
                    {"name": "No History Read", "datasetId": "d2", "webUrl": "u2"},
                    {"name": "Never Refreshable", "datasetId": "d3", "webUrl": "u3"},
                    {"name": "Genuinely Empty", "datasetId": "d4", "webUrl": "u4"},
                ]
            },
            "/groups/ws-1/datasets": {
                "value": [
                    {"id": "d1", "name": "One", "isRefreshable": True},
                    {"id": "d2", "name": "Two"},
                    {"id": "d3", "name": "Three", "isRefreshable": False},
                    {"id": "d4", "name": "Four", "isRefreshable": True},
                ]
            },
            "/datasets/d1/refreshSchedule": throttled,
            "/datasets/d1/refreshes": {
                "value": [{"status": "Completed", "endTime": "2026-08-28T09:00:00Z"}]
            },
            "/datasets/d2/refreshSchedule": {"enabled": True, "days": ["Monday"], "times": ["08:00"]},
            "/datasets/d2/refreshes": throttled,
            "/datasets/d3/refreshSchedule": throttled,
            "/datasets/d3/refreshes": throttled,
            "/datasets/d4/refreshSchedule": {"enabled": False, "days": [], "times": []},
            "/datasets/d4/refreshes": {"value": []},
        },
    )

    payload = pbi_fetch.fetch_refresh_payload("Governed Workspace")
    by_name = {entry["report_name"]: entry for entry in payload["reports"]}

    assert by_name["No Schedule Read"]["schedule_unreadable"] is True
    assert by_name["No Schedule Read"]["last_refresh_unreadable"] is False
    assert by_name["No Schedule Read"]["last_refresh"]["status"] == "Completed"

    assert by_name["No History Read"]["schedule_unreadable"] is False
    assert by_name["No History Read"]["last_refresh_unreadable"] is True
    assert by_name["No History Read"]["schedule"]["days"] == ["Monday"]

    # Power BI reports this dataset as never refreshed and unscheduled, so a
    # rejected read still means "there is none", exactly as before.
    assert by_name["Never Refreshable"]["schedule_unreadable"] is False
    assert by_name["Never Refreshable"]["last_refresh_unreadable"] is False

    # A readable, empty answer is "there is none" too.
    assert by_name["Genuinely Empty"]["schedule_unreadable"] is False
    assert by_name["Genuinely Empty"]["last_refresh_unreadable"] is False
    assert by_name["Genuinely Empty"]["last_refresh"] is None


def test_s04_unreadable_refresh_data_keeps_values_and_open_failed_alert(audit_db):
    _seed_report()
    failed_at = _iso(timedelta(hours=3))
    first = pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry(
                last_refresh={
                    "end_time": failed_at,
                    "status": "Failed",
                    "error": "Gateway could not reach the source.",
                }
            )
        )
    )
    assert first["status"] == "completed"
    assert _refresh_actions() == [("refresh_failed", "open")]

    # One failed Power BI call for each part: the fetch could read neither.
    second = pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry(
                schedule=dict(NO_SCHEDULE),
                last_refresh=None,
                schedule_unreadable=True,
                last_refresh_unreadable=True,
            )
        )
    )

    row = _report_row()
    assert row["pbi_refresh_schedule"] == DAILY_TEXT
    assert row["pbi_last_refresh_at"] == failed_at
    assert row["pbi_refresh_status"] == "Failed"
    assert row["pbi_refresh_error"] == "Gateway could not reach the source."
    assert row["archived"] == 0
    # The open failure is neither "recovered" nor replaced by an overdue alert.
    assert _refresh_actions() == [("refresh_failed", "open")]
    assert second["overdue_alerts"] == 0
    assert "UNREAD: Alpha - refresh schedule and refresh history could not be read" in second["log"]
    assert "OK: Alpha" not in second["log"]


def test_s04_unreadable_history_opens_no_overdue_or_never_refreshed_alert(audit_db):
    # Stored evidence is old enough that judging it again would say "overdue".
    stale_at = _iso(timedelta(days=10))
    _seed_report(
        pbi_refresh_schedule=DAILY_TEXT,
        pbi_last_refresh_at=stale_at,
        pbi_refresh_status="Completed",
    )

    result = pbi_sync.import_pbi_data(
        _refresh_payload(_refresh_entry(last_refresh=None, last_refresh_unreadable=True))
    )

    row = _report_row()
    assert row["pbi_last_refresh_at"] == stale_at
    assert row["pbi_refresh_status"] == "Completed"
    assert _refresh_actions() == []
    with database.get_db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM alerts WHERE message LIKE 'PBI refresh %'"
        ).fetchone()[0] == 0
    assert "UNREAD: Alpha - refresh history could not be read" in result["log"]

    # Once the history is readable again the same evidence is judged normally.
    readable = pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry(last_refresh={"end_time": stale_at, "status": "Completed"})
        )
    )
    assert readable["overdue_alerts"] == 1
    assert _refresh_actions() == [("refresh_overdue", "open")]


def test_s04_unreadable_schedule_keeps_schedule_and_still_reports_a_failed_refresh(audit_db):
    _seed_report(
        pbi_refresh_schedule=DAILY_TEXT,
        pbi_last_refresh_at=_iso(timedelta(hours=5)),
        pbi_refresh_status="Completed",
    )
    failed_at = _iso(timedelta(hours=1))

    result = pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry(
                schedule=dict(NO_SCHEDULE),
                schedule_unreadable=True,
                last_refresh={"end_time": failed_at, "status": "Failed", "error": "boom"},
            )
        )
    )

    row = _report_row()
    assert row["pbi_refresh_schedule"] == DAILY_TEXT
    assert row["pbi_last_refresh_at"] == failed_at
    assert row["pbi_refresh_status"] == "Failed"
    assert _refresh_actions() == [("refresh_failed", "open")]
    assert "UNREAD: Alpha - refresh schedule could not be read" in result["log"]


def test_s04_unreadable_schedule_opens_no_overdue_alert_from_the_stored_schedule(audit_db):
    stale_at = _iso(timedelta(days=10))
    _seed_report(pbi_refresh_schedule=DAILY_TEXT)

    pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry(
                schedule=dict(NO_SCHEDULE),
                schedule_unreadable=True,
                last_refresh={"end_time": stale_at, "status": "Completed"},
            )
        )
    )

    assert _report_row()["pbi_refresh_schedule"] == DAILY_TEXT
    assert _refresh_actions() == []


def test_s04_dataset_that_genuinely_has_no_schedule_or_history_behaves_as_before(audit_db):
    _seed_report()
    pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry(last_refresh={"end_time": _iso(timedelta(hours=3)), "status": "Failed"})
        )
    )
    assert _refresh_actions() == [("refresh_failed", "open")]

    # Readable and empty: the schedule was removed and there is no history.
    result = pbi_sync.import_pbi_data(
        _refresh_payload(_refresh_entry(schedule=dict(NO_SCHEDULE), last_refresh=None))
    )

    row = _report_row()
    assert row["pbi_refresh_schedule"] is None
    assert row["pbi_last_refresh_at"] is None
    assert row["pbi_refresh_status"] is None
    assert _refresh_actions() == [("refresh_failed", "resolved")]
    assert "OK: Alpha - no history (no schedule)" in result["log"]


# ---------------------------------------------------------------------------
# S-17: a report the workspace lists is present even without a local dataset
# ---------------------------------------------------------------------------

def test_s17_fetch_names_every_report_the_workspace_lists(monkeypatch):
    _fake_refresh_api(
        monkeypatch,
        {
            "/groups/ws-1/reports": {
                "value": [
                    {"name": "Local Model", "datasetId": "d1", "webUrl": "u1"},
                    {"name": "Shared Model", "datasetId": "other-workspace-dataset", "webUrl": "u2"},
                    {"name": "No Model", "webUrl": "u3"},
                ]
            },
            "/groups/ws-1/datasets": {"value": [{"id": "d1", "name": "One"}]},
            "/datasets/d1/refreshSchedule": {"enabled": False, "days": [], "times": []},
            "/datasets/d1/refreshes": {"value": []},
        },
    )

    payload = pbi_fetch.fetch_refresh_payload("Governed Workspace")

    assert [entry["report_name"] for entry in payload["reports"]] == ["Local Model"]
    assert payload["workspace_report_names"] == ["Local Model", "Shared Model", "No Model"]


def test_s17_report_listed_in_workspace_is_not_archived_without_its_dataset(audit_db):
    stored_at = _iso(timedelta(days=40))
    _seed_report(1, "Alpha")
    # Archived by every earlier sync because its dataset is in another workspace.
    _seed_report(
        2,
        "Shared Model",
        archived=1,
        pbi_dataset_id="stored-dataset",
        pbi_refresh_schedule=DAILY_TEXT,
        pbi_last_refresh_at=stored_at,
        pbi_refresh_status="Completed",
    )
    _seed_report(3, "Gone From Workspace")

    result = pbi_sync.import_pbi_data(
        _refresh_payload(
            _refresh_entry("Alpha"),
            workspace_report_names=["Alpha", " shared model ", "Not In Catalogue"],
        )
    )

    shared = _report_row(2)
    assert shared["archived"] == 0
    # No refresh information is written for it ...
    assert shared["pbi_dataset_id"] == "stored-dataset"
    assert shared["pbi_refresh_schedule"] == DAILY_TEXT
    assert shared["pbi_last_refresh_at"] == stored_at
    assert shared["pbi_refresh_status"] == "Completed"
    # ... and none is judged: the old stored refresh raises no overdue alert.
    assert _refresh_actions(2) == []
    assert "PRESENT: Shared Model (in PBI workspace" in result["log"]

    assert _report_row(1)["archived"] == 0
    # A report that is genuinely absent from the workspace is archived as before.
    assert _report_row(3)["archived"] == 1
    assert "ARCHIVE: Gone From Workspace (not in PBI workspace)" in result["log"]
    assert result["matched"] == 1
    assert result["unmatched"] == []


def test_s17_payload_without_workspace_list_archives_as_before(audit_db):
    # tools/pbi_refresh_sync.ps1 posts no workspace report list.
    _seed_report(1, "Alpha")
    _seed_report(2, "Shared Model")

    pbi_sync.import_pbi_data(_refresh_payload(_refresh_entry("Alpha")))

    assert _report_row(1)["archived"] == 0
    assert _report_row(2)["archived"] == 1


# ---------------------------------------------------------------------------
# S-19: archive count, proxy credentials, pg_cron run-history bound
# ---------------------------------------------------------------------------

def test_s19a_archive_count_and_log_cover_only_newly_archived_reports(audit_db):
    _seed_report(1, "Alpha")
    _seed_report(2, "Newly Gone")
    _seed_report(3, "Archived Earlier", archived=1)

    first = pbi_sync.import_pbi_data(_refresh_payload(_refresh_entry("Alpha")))

    assert first["archived"] == 1
    assert "ARCHIVE: Newly Gone (not in PBI workspace)" in first["log"]
    assert "Archived Earlier" not in first["log"]

    second = pbi_sync.import_pbi_data(_refresh_payload(_refresh_entry("Alpha")))

    assert second["archived"] == 0
    assert "ARCHIVE:" not in second["log"]
    assert _report_row(2)["archived"] == 1
    assert _report_row(3)["archived"] == 1


def test_s19b_connectivity_probe_never_shows_proxy_credentials(monkeypatch):
    from app.scanner import pbi_auth

    connected_to = []

    def refuse(address, timeout=None):
        connected_to.append(address)
        raise OSError("connection refused")

    monkeypatch.setattr(
        pbi_auth,
        "resolve_proxy",
        lambda _url: "http://svc-user:s3cr3t-p%40ss@proxy.internal:8080",
    )
    monkeypatch.setattr("socket.create_connection", refuse)

    findings = pbi_auth.probe_connectivity()

    assert findings.startswith("via proxy http://proxy.internal:8080;")
    assert "svc-user" not in findings
    assert "s3cr3t" not in findings
    # The credentials are only hidden from display; the probe still dials the proxy.
    assert connected_to == [("proxy.internal", 8080)]


def test_s19b_resolved_system_proxy_is_logged_without_credentials(monkeypatch, caplog):
    import logging
    from types import SimpleNamespace

    from app.scanner import pbi_auth

    monkeypatch.setattr(pbi_auth, "PBI_PROXY", "")
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pbi_auth, "_PROXY_CACHE", {})
    monkeypatch.setattr(pbi_auth.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pbi_auth.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="http://svc-user:s3cr3t@proxy.internal:8080/\n"
        ),
    )

    with caplog.at_level(logging.INFO, logger=pbi_auth.logger.name):
        proxy = pbi_auth.resolve_proxy("https://api.powerbi.com/v1.0/myorg")

    # The caller still gets the working address, credentials included.
    assert proxy == "http://svc-user:s3cr3t@proxy.internal:8080"
    assert "Outbound proxy for api.powerbi.com: http://proxy.internal:8080" in caplog.text
    assert "s3cr3t" not in caplog.text
    assert "svc-user" not in caplog.text


@pytest.mark.parametrize(
    ("proxy", "shown"),
    [
        ("http://proxy.internal:8080", "http://proxy.internal:8080"),
        ("http://user:p@ss@proxy.internal:8080", "http://proxy.internal:8080"),
        ("user:secret@proxy.internal:8080", "proxy.internal:8080"),
        ("http://user@proxy.internal:8080/path", "http://proxy.internal:8080/path"),
        ("http://user:pa/ss@proxy.internal:8080", "http://proxy.internal:8080"),
        ("user:p://ss@proxy.internal:8080", "proxy.internal:8080"),
        (None, ""),
    ],
    ids=[
        "no-userinfo",
        "user-and-password",
        "no-scheme",
        "user-only",
        "slash-in-password",
        "scheme-like-password",
        "no-proxy",
    ],
)
def test_s19b_proxy_display_strips_only_the_userinfo(proxy, shown):
    from app.scanner import pbi_auth

    assert pbi_auth._proxy_for_display(proxy) == shown


def test_s19c_pg_cron_run_history_query_is_bounded_and_read_only(audit_db, monkeypatch):
    from app.scanner import pg_cron

    executed = []

    class Cursor:
        query = ""

        def execute(self, query):
            self.query = str(query)
            executed.append(self.query)

        def fetchone(self):
            if "FROM pg_extension" in self.query:
                return (True, True, True, True, True)
            if "current_setting('cron.timezone'" in self.query:
                return (None, "UTC")
            return None

        def fetchall(self):
            if "FROM cron.job ORDER BY" in self.query:
                return [
                    (
                        17,
                        "15 6 * * *",
                        'REFRESH MATERIALIZED VIEW "sales"."daily_totals"',
                        "staging",
                        "cron_user",
                        True,
                    )
                ]
            return []

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(pg_cron, "PGHOST", "primary.internal")
    monkeypatch.setattr(pg_cron, "PGPORT", 5432)
    monkeypatch.setattr(pg_cron, "_get_pg_connection", lambda: Connection())

    pg_cron.scan_pg_cron()

    history = [query for query in executed if "cron.job_run_details" in query]
    assert len(history) == 1
    statement = " ".join(history[0].split())
    assert statement.startswith("SELECT DISTINCT ON (jobid)")
    assert "WHERE start_time >= now() - interval '7 days'" in statement
    for query in executed:
        assert query.lstrip().upper().startswith("SELECT")


# ---------------------------------------------------------------------------
# Prober fixtures shared by S-10, S-13 and S-14
# ---------------------------------------------------------------------------

PROBE_NOW = "2026-09-06T02:00:00+00:00"
# 21:30 UTC on 5 September is 01:30 on 6 September in Dubai (UTC+4, no DST).
LATE_EVENING_UTC = datetime(2026, 9, 5, 21, 30, tzinfo=timezone.utc)


def _seed_source(db, source_id, name, source_type="postgresql", **fields):
    columns = ["id", "name", "type", "connection_info", "discovered_by", "archived", *fields]
    values = [source_id, name, source_type, name, "test", 0, *fields.values()]
    db.execute(
        f"INSERT INTO sources ({', '.join(columns)}) VALUES ({', '.join('?' for _ in values)})",
        values,
    )


def _seed_probe(db, source_id, last_data_at, probed_at=PROBE_NOW, status="fresh", row_count=10):
    db.execute(
        """INSERT INTO source_probes (source_id, probed_at, last_data_at, row_count, status)
           VALUES (?, ?, ?, ?, ?)""",
        (source_id, probed_at, last_data_at, row_count, status),
    )


def _sql_text(query) -> str:
    """Render a psycopg2 composable without a live connection."""
    from psycopg2 import sql

    if isinstance(query, sql.Composed):
        return "".join(_sql_text(part) for part in query.seq)
    if isinstance(query, sql.Identifier):
        return ".".join(f'"{part}"' for part in query.strings)
    if isinstance(query, sql.SQL):
        return query.string
    return str(query)


class _ProbePgCursor:
    """Answers the prober's per-relation SELECTs from a relation -> row map."""

    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def execute(self, query, params=None):
        text = _sql_text(query)
        self.connection.queries.append(text)
        for relation, answer in self.connection.answers.items():
            if f'"{relation}"' in text:
                self.row = answer(text) if callable(answer) else answer
                return
        raise AssertionError(f"Unexpected PostgreSQL query: {text}")

    def fetchone(self):
        return self.row


class _ProbePgConnection:
    def __init__(self, answers):
        self.answers = answers
        self.queries = []
        self.closed = False

    def cursor(self):
        return _ProbePgCursor(self)

    def close(self):
        self.closed = True


def _run_pg_probe(monkeypatch, connection):
    from app.checks import data_quality
    from app.scanner import prober

    monkeypatch.setattr(prober, "PGHOST", "primary.internal")
    monkeypatch.setattr(prober, "PGPORT", 5432)
    monkeypatch.setattr(prober, "UPLOAD_PGHOST", "")
    monkeypatch.setattr(prober, "_get_pg_connection", lambda *, database=None: connection)
    monkeypatch.setattr(data_quality, "run_quality_checks", lambda: {})
    return prober.run_probe()


def _seed_pg_source(db, source_id, relation, kind="table", **fields):
    from app.source_identity import upsert_postgres_identity

    _seed_source(db, source_id, f"primary.internal/warehouse/sales.{relation}", **fields)
    result = upsert_postgres_identity(
        db,
        source_id=source_id,
        server="primary.internal",
        database="warehouse",
        schema="sales",
        relation=relation,
        relation_kind=kind,
        verified_at=PROBE_NOW,
    )
    assert result["status"] in {"claimed", "refreshed"}


def _latest_probe(source_id) -> dict:
    with database.get_db() as db:
        return dict(
            db.execute(
                """SELECT status, row_count, message, CAST(last_data_at AS TEXT) AS last_data_at
                     FROM source_probes WHERE source_id = ? ORDER BY id DESC LIMIT 1""",
                (source_id,),
            ).fetchone()
        )


# ---------------------------------------------------------------------------
# S-10: alert and status text shows Dubai time; storage stays UTC
# ---------------------------------------------------------------------------

def test_s10_file_probe_message_shows_dubai_time_and_stores_utc(audit_db, tmp_path):
    import os

    from app.scanner import prober

    path = tmp_path / "orders.csv"
    path.write_text("id\n1\n2\n", encoding="utf-8")
    os.utime(path, (LATE_EVENING_UTC.timestamp(), LATE_EVENING_UTC.timestamp()))

    with database.get_db() as db:
        _seed_source(db, 1, str(path), source_type="csv")
        prober._probe_file_source(db, 1, str(path), PROBE_NOW, {"type": None})

    probe = _latest_probe(1)
    assert probe["message"].startswith("File modified: 2026-09-06 01:30 Dubai time (2 rows)")
    assert probe["message"].count("Dubai time") == 1
    assert probe["last_data_at"] == "2026-09-05T21:30:00+00:00"


def test_s10_postgres_probe_message_shows_dubai_time_not_the_session_zone(audit_db, monkeypatch):
    # The driver returns the commit timestamp in the database session's zone.
    session_zone = timezone(timedelta(hours=-5))
    last_write = LATE_EVENING_UTC.astimezone(session_zone)
    with database.get_db() as db:
        _seed_pg_source(db, 1, "orders")

    _run_pg_probe(monkeypatch, _ProbePgConnection({"orders": (last_write, 1234)}))

    probe = _latest_probe(1)
    assert probe["message"].startswith("Last write: 2026-09-06 01:30 Dubai time (1,234 rows)")
    assert probe["message"].count("Dubai time") == 1
    # Stored exactly as before: the instant as PostgreSQL returned it.
    assert probe["last_data_at"] == last_write.isoformat()


def test_s10_report_behind_source_message_shows_dubai_time(audit_db):
    from app.scanner import prober

    _seed_report(pbi_last_refresh_at="2026-09-05T20:00:00Z")
    with database.get_db() as db:
        _seed_source(db, 1, "primary.internal/warehouse/sales.orders")
        db.execute("INSERT INTO report_tables (report_id, table_name, source_id) VALUES (1, 'Orders', 1)")
        _seed_probe(db, 1, "2026-09-05T23:30:00+00:00")
        prober._check_report_source_schedule(db, PROBE_NOW, [])
        notes = db.execute(
            "SELECT notes FROM actions WHERE type = 'schedule_mismatch'"
        ).fetchone()["notes"]
        stored = db.execute("SELECT pbi_last_refresh_at FROM reports WHERE id = 1").fetchone()[0]

    assert notes == (
        "Report last refreshed 2026-09-06 00:00 but source sales.orders has data from "
        "2026-09-06 03:30 (Dubai time, 3h behind)"
    )
    assert stored == "2026-09-05T20:00:00Z"


def test_s10_dependency_alert_shows_dubai_time_and_still_dedupes_and_resolves(audit_db):
    from app.scanner import prober

    with database.get_db() as db:
        _seed_source(db, 1, "primary.internal/warehouse/sales.daily_mv")
        _seed_source(db, 2, "primary.internal/warehouse/sales.orders")
        db.execute(
            """INSERT INTO source_dependencies (source_id, depends_on_id, discovered_by, created_at)
               VALUES (1, 2, 'test', ?)""",
            (PROBE_NOW,),
        )
        _seed_probe(db, 1, "2026-09-05T20:00:00+00:00")
        _seed_probe(db, 2, "2026-09-05T23:30:00+00:00")

        prober._check_dependency_freshness(db, PROBE_NOW, [])
        prober._check_dependency_freshness(db, PROBE_NOW, [])
        alerts = db.execute("SELECT message, resolution_status FROM alerts").fetchall()
        assert [(row["message"], row["resolution_status"]) for row in alerts] == [
            (
                "Upstream sales.orders has data from 2026-09-06 03:30 but MV sales.daily_mv "
                "last refreshed 2026-09-06 00:00 (Dubai time, 3h behind)",
                None,
            )
        ]

        # The materialized view catches up: the alert written above is found and closed.
        _seed_probe(db, 1, "2026-09-06T00:30:00+00:00", probed_at="2026-09-06T03:00:00+00:00")
        prober._check_dependency_freshness(db, "2026-09-06T03:00:00+00:00", [])
        assert db.execute("SELECT resolution_status FROM alerts").fetchone()[0] == "resolved"


def test_s10_overdue_alert_shows_last_refresh_in_dubai_time(audit_db):
    _seed_report(
        1,
        "Alpha",
        pbi_refresh_schedule=DAILY_TEXT,
        pbi_last_refresh_at="2026-09-05T21:30:00Z",
        pbi_refresh_status="Completed",
    )
    _seed_report(2, "Never Refreshed", pbi_refresh_schedule=DAILY_TEXT)

    with database.get_db() as db:
        assert pbi_sync._check_refresh_alerts(db, _iso()) == 2
        notes = {
            row["report_id"]: row["notes"]
            for row in db.execute("SELECT report_id, notes FROM actions").fetchall()
        }
        stored = db.execute("SELECT pbi_last_refresh_at FROM reports WHERE id = 1").fetchone()[0]

    assert notes[1] == (
        f"PBI refresh overdue: Alpha (schedule: {DAILY_TEXT}, "
        "last refresh: 2026-09-06 01:30 Dubai time)"
    )
    assert notes[2].endswith("last refresh: never)")
    assert stored == "2026-09-05T21:30:00Z"


# ---------------------------------------------------------------------------
# S-13: a timed rule that is not due yet is not "fresh" without any evidence
# ---------------------------------------------------------------------------

def _timed_rule(baseline: datetime) -> dict:
    return {
        "type": "daily",
        "time": "06:00",
        "days": [],
        "day": None,
        "timezone": "Asia/Dubai",
        "baseline_at": baseline.isoformat(),
        "description": "daily",
    }


def test_s13_pending_timed_rule_without_evidence_is_unknown_not_fresh():
    from app.scanner import prober

    now = datetime.now(timezone.utc)
    pending = _timed_rule(now)

    assert prober._compute_status_for_rule(None, pending) == "unknown"
    assert prober._compute_status_for_rule("", pending) == "unknown"


def test_s13_every_other_timed_rule_outcome_is_unchanged():
    from app.scanner import prober

    now = datetime.now(timezone.utc)
    pending = _timed_rule(now)
    enforced = _timed_rule(now - timedelta(days=5))

    # Still inside the first grace period, with evidence from before the
    # baseline: reported fresh, as before.
    assert prober._compute_status_for_rule((now - timedelta(days=30)).isoformat(), pending) == "fresh"
    assert prober._compute_status_for_rule(now.isoformat(), pending) == "fresh"
    # An occurrence is enforceable: missing or old evidence is outdated,
    # evidence after it is fresh.
    assert prober._compute_status_for_rule(None, enforced) == "outdated"
    assert prober._compute_status_for_rule((now - timedelta(days=30)).isoformat(), enforced) == "outdated"
    assert prober._compute_status_for_rule(now.isoformat(), enforced) == "fresh"


def test_s13_newly_mapped_rule_does_not_turn_an_evidence_free_probe_fresh(audit_db):
    from app import freshness_inheritance

    with database.get_db() as db:
        _seed_source(
            db,
            1,
            "primary.internal/warehouse/sales.orders",
            freshness_mode="inherit",
            freshness_rule_status="mapped",
            freshness_rule_type="daily",
            freshness_schedule_time="06:00",
            freshness_timezone="Asia/Dubai",
            freshness_effective_from_at=_iso(),
        )
        db.execute(
            """INSERT INTO source_probes (source_id, probed_at, status, message)
               VALUES (1, ?, 'unknown', 'Query failed on exact target')""",
            (_iso(),),
        )
        freshness_inheritance._recalculate_latest_probe(db, 1)

    assert _latest_probe(1)["status"] == "unknown"


# ---------------------------------------------------------------------------
# S-14: plain views and foreign tables are probed without xmin
# ---------------------------------------------------------------------------

def _relation_without_xmin(row_count):
    """Answer like PostgreSQL does for a relation that has no xmin column."""

    def answer(text):
        if "xmin" in text:
            raise RuntimeError('column "xmin" does not exist')
        return (row_count,)

    return answer


def test_s14_views_and_foreign_tables_record_row_count_without_commit_timestamps(
    audit_db, monkeypatch
):
    with database.get_db() as db:
        _seed_pg_source(db, 1, "orders", "table")
        _seed_pg_source(db, 2, "orders_view", "view", freshness_rule_type="daily", custom_fresh_days=1)
        _seed_pg_source(db, 3, "remote_orders", "foreign_table")
        _seed_pg_source(db, 4, "orders_mv", "materialized_view")
    connection = _ProbePgConnection(
        {
            "orders": (LATE_EVENING_UTC, 10),
            "orders_view": _relation_without_xmin(7),
            "remote_orders": _relation_without_xmin(3),
            "orders_mv": (LATE_EVENING_UTC, 5),
        }
    )

    result = _run_pg_probe(monkeypatch, connection)

    view = _latest_probe(2)
    assert view["status"] == "unknown"
    assert view["row_count"] == 7
    assert view["last_data_at"] is None
    assert view["message"] == (
        "A view carries no commit timestamps, so its freshness cannot be read here; "
        "set the freshness rule on the tables underneath (7 rows)"
    )
    foreign = _latest_probe(3)
    assert foreign["status"] == "unknown"
    assert foreign["row_count"] == 3
    assert foreign["message"].startswith("A foreign table carries no commit timestamps")
    assert result["statuses"]["unknown"] == 2

    # Tables and materialized views are probed exactly as before.
    for source_id, rows in ((1, 10), (4, 5)):
        probe = _latest_probe(source_id)
        assert probe["message"].startswith("Last write: ")
        assert probe["row_count"] == rows
        assert probe["last_data_at"] == LATE_EVENING_UTC.isoformat()

    by_relation = {
        relation: [query for query in connection.queries if f'"{relation}"' in query]
        for relation in ("orders", "orders_view", "remote_orders", "orders_mv")
    }
    assert by_relation["orders_view"] == ['SELECT COUNT(*) AS row_count FROM "sales"."orders_view"']
    assert by_relation["remote_orders"] == ['SELECT COUNT(*) AS row_count FROM "sales"."remote_orders"']
    assert "pg_xact_commit_timestamp(xmin)" in by_relation["orders"][0]
    assert "pg_xact_commit_timestamp(xmin)" in by_relation["orders_mv"][0]
    # The probe credentials stay read-only: nothing but SELECT is sent.
    assert all(query.startswith("SELECT ") for query in connection.queries)
    with database.get_db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM actions WHERE source_id IN (2, 3)"
        ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# S-11: no SQLite write transaction is open while a PostgreSQL check runs
# ---------------------------------------------------------------------------

def test_s11_quality_run_releases_the_sqlite_write_lock_before_each_postgres_query(
    audit_db, monkeypatch
):
    import json
    import sqlite3

    from app.checks import data_quality

    with database.get_db() as db:
        _seed_source(db, 1, "Orders file", source_type="excel")
        _seed_probe(db, 1, None, row_count=50)
        _seed_pg_source(db, 2, "orders")
        db.execute(
            """INSERT INTO checks (id, name, source_id, type, config, severity, enabled)
               VALUES (1, 'Enough rows', 1, 'row_count_range', ?, 'critical', 1)""",
            (json.dumps({"min_rows": 100}),),
        )
        for check_id in (2, 3):
            db.execute(
                """INSERT INTO checks (id, name, source_id, type, config, severity, enabled)
                   VALUES (?, 'Null rate', 2, 'null_rate', ?, 'warning', 1)""",
                (check_id, json.dumps({"column": "customer_id", "max_null_pct": 5})),
            )

    seen_during_postgres = []

    class Cursor:
        def execute(self, query, params=None):
            assert _sql_text(query).startswith("SELECT ")
            # What another writer (a Flow run, a scan heartbeat) would meet
            # while this PostgreSQL query is running.
            other = sqlite3.connect(database.DB_PATH, timeout=0)
            try:
                committed = other.execute(
                    "SELECT COUNT(*) FROM check_results"
                ).fetchone()[0]
                actions = other.execute(
                    "SELECT COUNT(*) FROM actions WHERE type = 'data_quality'"
                ).fetchone()[0]
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.rollback()
                    blocked = False
                except sqlite3.OperationalError:
                    blocked = True
            finally:
                other.close()
            seen_during_postgres.append((blocked, committed, actions))

        def fetchone(self):
            return (200, 50)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(data_quality, "PGHOST", "primary.internal")
    monkeypatch.setattr(data_quality, "PGPORT", 5432)
    monkeypatch.setattr(data_quality, "UPLOAD_PGHOST", "")
    monkeypatch.setattr(data_quality, "_get_pg_connection", lambda *, database=None: Connection())

    result = data_quality.run_quality_checks()

    assert result["total"] == 3
    assert result["fail"] == 3
    # During each PostgreSQL query the write lock is free and every earlier
    # check's result and incident are already committed together.
    assert seen_during_postgres == [(False, 1, 1), (False, 2, 2)]
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM check_results").fetchone()[0] == 3
        assert db.execute(
            "SELECT COUNT(*) FROM actions WHERE type = 'data_quality' AND status = 'open'"
        ).fetchone()[0] == 3
        assert db.execute(
            "SELECT COUNT(*) FROM checks WHERE updated_at = (SELECT MAX(ran_at) FROM check_results)"
        ).fetchone()[0] == 3


# ---------------------------------------------------------------------------
# S-12: the manual materialized-view refresh has lock and statement limits
# ---------------------------------------------------------------------------

class _PgError(Exception):
    """Shaped like a psycopg2 error: a message plus an SQLSTATE."""

    def __init__(self, message, pgcode):
        super().__init__(message)
        self.pgcode = pgcode


class _RefreshEngine:
    """Records what the manual refresh sends through the Flow SQL engine."""

    def __init__(self, refresh_error=None):
        self.statements = []
        self.refresh_error = refresh_error
        self.disposed = False

    def _connection(self):
        engine = self

        class Result:
            def fetchone(self):
                return (1,)

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def execute(self, statement, params=None):
                text = str(statement)
                engine.statements.append(text)
                if text.startswith("REFRESH") and engine.refresh_error is not None:
                    raise engine.refresh_error
                return Result()

        return Connection()

    def begin(self):
        return self._connection()

    def connect(self):
        return self._connection()

    def dispose(self):
        self.disposed = True


@pytest.fixture
def refreshable_view(audit_db, monkeypatch):
    from app import flow_sql
    from app.routers import materialized_views
    from app.source_identity import upsert_postgres_identity

    monkeypatch.setattr(materialized_views, "UPLOAD_PGHOST", "flow.internal")
    monkeypatch.setattr(materialized_views, "UPLOAD_PGPORT", 5432)
    with database.get_db() as db:
        _seed_source(db, 1, "flow.internal/warehouse/sales.daily_totals")
        upsert_postgres_identity(
            db,
            source_id=1,
            server="flow.internal",
            database="warehouse",
            schema="sales",
            relation="daily_totals",
            relation_kind="materialized_view",
            verified_at=PROBE_NOW,
        )

    def install(engine):
        databases = []

        def factory(database_name, credentials=None):
            databases.append(database_name)
            return engine

        # flow_sql._engine is the DG_UPLOAD_PG* connection, never the probe one.
        monkeypatch.setattr(flow_sql, "_engine", factory)
        return databases

    return install


def _refresh_request():
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(actor="Test User"))


def test_s12_manual_view_refresh_sets_lock_and_statement_timeouts_first(refreshable_view):
    from app import flow_view_refresh
    from app.routers import materialized_views

    engine = _RefreshEngine()
    databases = refreshable_view(engine)

    result = materialized_views.refresh_materialized_view(1, _refresh_request())

    assert result == {"refreshed": "warehouse.sales.daily_totals", "source_id": 1}
    assert databases == ["warehouse"]
    assert engine.disposed is True
    assert engine.statements[-3:] == [
        f"SET LOCAL lock_timeout = '{flow_view_refresh.LOCK_TIMEOUT_MS}ms'",
        f"SET LOCAL statement_timeout = '{flow_view_refresh.STATEMENT_TIMEOUT_MS}ms'",
        'REFRESH MATERIALIZED VIEW "sales"."daily_totals"',
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "explanation"),
    [
        (
            _PgError("canceling statement due to lock timeout", "55P03"),
            409,
            "kept the view locked for more than 10 minutes",
        ),
        (
            _PgError("canceling statement due to statement timeout", "57014"),
            504,
            "stopped after running for 60 minutes (statement timeout)",
        ),
    ],
    ids=["lock-timeout", "statement-timeout"],
)
def test_s12_view_refresh_timeout_is_explained(refreshable_view, error, status_code, explanation):
    from fastapi import HTTPException

    from app.routers import materialized_views

    engine = _RefreshEngine(refresh_error=error)
    refreshable_view(engine)

    with pytest.raises(HTTPException) as raised:
        materialized_views.refresh_materialized_view(1, _refresh_request())

    assert raised.value.status_code == status_code
    assert explanation in raised.value.detail
    assert "canceling statement" not in raised.value.detail
    assert engine.disposed is True


def test_s12_other_refresh_failures_keep_the_existing_502_message(refreshable_view):
    from fastapi import HTTPException

    from app.routers import materialized_views

    engine = _RefreshEngine(refresh_error=_PgError("permission denied for materialized view", "42501"))
    refreshable_view(engine)

    with pytest.raises(HTTPException) as raised:
        materialized_views.refresh_materialized_view(1, _refresh_request())

    assert raised.value.status_code == 502
    assert raised.value.detail.startswith("Materialized view refresh failed: ")
    assert "permission denied" in raised.value.detail


# ---------------------------------------------------------------------------
# S-16: usage numbers
# ---------------------------------------------------------------------------

def test_s16a_usage_day_cut_off_at_the_page_cap_is_not_marked_synced(monkeypatch, caplog):
    import logging

    today = datetime.now(timezone.utc).date()
    busy_day = (today - timedelta(days=1)).isoformat()
    exact_day = (today - timedelta(days=2)).isoformat()
    quiet_day = (today - timedelta(days=3)).isoformat()
    pages_served = {busy_day: 0, exact_day: 0, quiet_day: 0}

    def view(report):
        return {"Activity": "ViewReport", "ReportName": report, "UserId": "analyst@example.test"}

    def fake_get_json(_client, _token, url, params=None):
        day = url.rsplit("day=", 1)[1] if params is None else params["startDateTime"][1:11]
        pages_served[day] += 1
        more = {"continuationToken": "next", "continuationUri": f"https://api.test/next?day={day}"}
        if day == busy_day:
            # Power BI always has another page for this day.
            return {"activityEventEntities": [view("Busy")], **more}
        if day == exact_day and pages_served[day] < 3:
            return {"activityEventEntities": [view("Exact")], **more}
        return {"activityEventEntities": [view("Exact" if day == exact_day else "Quiet")], "lastResultSet": True}

    monkeypatch.setattr(pbi_fetch, "MAX_ACTIVITY_PAGES_PER_DAY", 3)
    monkeypatch.setattr(pbi_fetch, "get_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(pbi_fetch, "_already_synced_usage_days", lambda: set())
    monkeypatch.setattr(pbi_fetch, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(pbi_fetch.httpx, "Client", _FakeHttpClient)
    monkeypatch.setattr(pbi_fetch, "_get_json", fake_get_json)

    with caplog.at_level(logging.WARNING, logger=pbi_fetch.logger.name):
        result = pbi_fetch.fetch_usage_payload(days_back=3)

    assert pages_served == {busy_day: 3, exact_day: 3, quiet_day: 1}
    # The cut-off day is not recorded as synced, so the next sync fetches it again.
    assert busy_day not in result["days_synced"]
    # A day whose last page is exactly the cap page is complete.
    assert sorted(result["days_synced"]) == [quiet_day, exact_day]
    assert result["status"] == "completed_with_warnings"
    assert result["successful_days"] == 2
    assert result["failed_days"] == 1
    assert f"Usage fetch for {busy_day} stopped at the 3-page limit" in caplog.text
    # What was read for the cut-off day is still reported.
    assert {"report_name": "Busy", "date": busy_day, "view_count": 3, "unique_users": 1} in result["entries"]


def _write_usage_csv(folder, rows):
    folder.mkdir(exist_ok=True)
    lines = ["Date,ReportName,UserId", *(",".join(row) for row in rows)]
    (folder / "Report_views.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _usage_tables(db) -> dict:
    return {
        "days": [row[0] for row in db.execute("SELECT date FROM pbi_usage_days ORDER BY date")],
        "views": [
            tuple(row)
            for row in db.execute(
                """SELECT report_name, view_date, view_count, unique_users
                     FROM pbi_report_views ORDER BY view_date, report_name"""
            )
        ],
        "user_views": db.execute("SELECT COUNT(*) FROM pbi_report_user_views").fetchone()[0],
    }


@pytest.fixture
def usage_csv(audit_db, tmp_path, monkeypatch):
    from app import usage

    folder = tmp_path / "usage-files"
    monkeypatch.setattr(usage, "USAGE_FILES_PATH", str(folder))
    monkeypatch.setattr(usage, "_LAST_CSV_SIGNATURE", None)
    return folder


def test_s16b_csv_import_keeps_days_and_rows_the_power_bi_api_sync_stored(usage_csv):
    from app import usage

    _seed_report(1, "Alpha")
    _seed_report(2, "Beta")
    with database.get_db() as db:
        # Stored by the Power BI API usage sync, which never writes per-user rows.
        for day in ("2026-09-10", "2026-09-12"):
            db.execute("INSERT INTO pbi_usage_days (date, synced_at) VALUES (?, ?)", (day, PROBE_NOW))
        db.executemany(
            """INSERT INTO pbi_report_views (report_name, report_id, view_date, view_count, unique_users)
               VALUES (?, ?, ?, ?, ?)""",
            [
                ("Alpha", 1, "2026-09-10", 99, 9),
                ("Beta", 2, "2026-09-10", 4, 1),
                ("Alpha", 1, "2026-09-12", 5, 2),
            ],
        )

    _write_usage_csv(
        usage_csv,
        [
            ("2026-09-09", "Alpha", "ana@example.test"),
            ("2026-09-10", "Alpha", "ana@example.test"),
            ("2026-09-10", "Alpha", "ben@example.test"),
        ],
    )
    with database.get_db() as db:
        first = usage.sync_usage_from_csv(db, force=True)
        tables = _usage_tables(db)

    assert first["status"] == "completed"
    # 12 September came from the API only: still synced, so it is not refetched.
    assert tables["days"] == ["2026-09-09", "2026-09-10", "2026-09-12"]
    assert tables["views"] == [
        ("Alpha", "2026-09-09", 1, 1),
        ("Alpha", "2026-09-10", 2, 2),  # the CSV wins for a report and day both have
        ("Beta", "2026-09-10", 4, 1),
        ("Alpha", "2026-09-12", 5, 2),
    ]

    # A newer export replaces the rows the previous CSV import stored, and only those.
    _write_usage_csv(usage_csv, [("2026-09-11", "Alpha", "ana@example.test")])
    with database.get_db() as db:
        usage.sync_usage_from_csv(db, force=True)
        tables = _usage_tables(db)

    assert tables["days"] == ["2026-09-11", "2026-09-12"]
    assert tables["views"] == [
        ("Beta", "2026-09-10", 4, 1),
        ("Alpha", "2026-09-11", 1, 1),
        ("Alpha", "2026-09-12", 5, 2),
    ]
    assert tables["user_views"] == 1


def test_s16b_csv_only_install_still_replaces_everything_on_each_import(usage_csv):
    from app import usage

    _seed_report(1, "Alpha")
    _write_usage_csv(
        usage_csv,
        [
            ("2026-09-01", "Alpha", "ana@example.test"),
            ("2026-09-02", "Alpha", "ana@example.test"),
            ("2026-09-02", "Unlisted", "ben@example.test"),
        ],
    )
    with database.get_db() as db:
        usage.sync_usage_from_csv(db, force=True)
        assert _usage_tables(db)["days"] == ["2026-09-01", "2026-09-02"]

    _write_usage_csv(usage_csv, [("2026-09-03", "Alpha", "cara@example.test")])
    with database.get_db() as db:
        usage.sync_usage_from_csv(db, force=True)
        tables = _usage_tables(db)

    assert tables == {
        "days": ["2026-09-03"],
        "views": [("Alpha", "2026-09-03", 1, 1)],
        "user_views": 1,
    }


# ---------------------------------------------------------------------------
# F-23: trend buckets use the Dubai calendar date they are labelled with
# ---------------------------------------------------------------------------

def _dubai_yesterday_instants():
    """Two UTC instants on yesterday's Dubai date: 01:30 and 12:00 Dubai time."""
    from zoneinfo import ZoneInfo

    from app import flow_clock

    yesterday = flow_clock.dubai_today() - timedelta(days=1)
    zone = ZoneInfo(flow_clock.TIMEZONE)
    small_hours = datetime(yesterday.year, yesterday.month, yesterday.day, 1, 30, tzinfo=zone)
    midday = small_hours.replace(hour=12, minute=0)
    return yesterday, small_hours.astimezone(timezone.utc), midday.astimezone(timezone.utc)


def test_f23_alert_trend_counts_small_hours_events_on_their_dubai_day(audit_db):
    from app.routers import schedules

    yesterday, small_hours_utc, midday_utc = _dubai_yesterday_instants()
    # 01:30 in Dubai is still the previous calendar day in UTC.
    assert small_hours_utc.date() == yesterday - timedelta(days=1)
    with database.get_db() as db:
        db.execute(
            "INSERT INTO alerts (severity, message, created_at) VALUES ('warning', 'iso', ?)",
            (small_hours_utc.isoformat(),),
        )
        db.execute(
            "INSERT INTO alerts (severity, message, created_at) VALUES ('warning', 'legacy', ?)",
            ((small_hours_utc + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),),
        )
        db.execute(
            "INSERT INTO alerts (severity, message, created_at) VALUES ('warning', 'midday', ?)",
            (midday_utc.isoformat(),),
        )

    trend = {item["day"]: item["count"] for item in schedules.get_alert_trend()}

    assert len(trend) == 30
    assert trend[yesterday.isoformat()] == 3
    assert trend[(yesterday - timedelta(days=1)).isoformat()] == 0


def test_f23_health_trend_places_a_small_hours_probe_on_its_dubai_day(audit_db):
    from app.routers import schedules

    yesterday, small_hours_utc, _midday_utc = _dubai_yesterday_instants()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO probe_runs
                   (started_at, finished_at, sources_probed, fresh, stale,
                    outdated, unknown, no_rule, status, log)
               VALUES (?, ?, 4, 3, 0, 1, 0, 0, 'completed', '')""",
            (small_hours_utc.isoformat(), small_hours_utc.isoformat()),
        )

    trend = schedules.get_health_trend()

    # The series starts on the first Dubai day that has a probe, not the day before.
    assert trend[0] == {"day": yesterday.isoformat(), "healthy": 3, "degraded": 1, "unknown": 0}
    assert (yesterday - timedelta(days=1)).isoformat() not in {item["day"] for item in trend}
