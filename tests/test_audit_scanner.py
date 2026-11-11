"""Regression tests for the scanner and scheduler audit findings."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import types
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import database, main, settings, user_identity
from app.routers import archive
from app.routers import scanner as scanner_router
from app.scanner import (
    control,
    jobs,
    layout_parser,
    modules,
    notifications,
    pbix_parser,
    runner,
    walker,
)
from app.scanner.pbix_parser import MeasureInfo
from app.scanner.tmdl_parser import ParsedTable, SourceInfo, _parse_m_expression
from app.scanner.walker import DiscoveredReport
from app.source_identity import (
    exact_identity_rows,
    postgres_server_identity,
    upsert_postgres_identity,
)


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "governance.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(main, "DB_PATH", db_path)
    database.init_db()
    return db_path


# F-23: a view retry starts production work and must wait out an update drain.


def test_view_retry_is_refused_while_an_update_is_draining(audit_db, monkeypatch):
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVITY_LOCK", threading.Lock())
    drain = threading.Event()
    drain.set()
    monkeypatch.setattr(main, "_AUTO_UPDATE_DRAIN_EVENT", drain)
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVE_STARTS", 0)
    client = TestClient(main.app)

    views = client.post("/api/flows/runs/999/retry-views")
    sql = client.post("/api/flows/runs/999/retry-sql")

    assert sql.status_code == 503
    assert views.status_code == 503
    assert views.headers["retry-after"] == "60"
    assert "finishing active work" in views.json()["detail"]


# S-07: unrecognised Power Query text can carry a connection string.

_SECRET_M = (
    'let Source = Custom.Connector("Server=db.internal;Password=hunter2") in Source'
)


class _Frame:
    """The small part of a pandas DataFrame the PBIX parser reads."""

    def __init__(self, columns, rows):
        self.columns = columns
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        return enumerate(self._rows)


def test_unknown_tmdl_source_is_logged_without_its_power_query_text(caplog):
    from app.scanner import tmdl_parser

    with caplog.at_level(logging.DEBUG):
        parsed = tmdl_parser._parse_m_expression(_SECRET_M)

    assert parsed.source_type == "unknown"
    assert "hunter2" not in caplog.text
    assert "db.internal" not in caplog.text
    assert "Custom.Connector" in caplog.text
    assert f"expression length: {len(_SECRET_M)}" in caplog.text


def test_unknown_pbix_source_is_logged_without_its_power_query_text(
    caplog, monkeypatch, tmp_path
):
    from app.scanner import pbix_parser

    class _Model:
        power_query = _Frame(
            ["TableName", "Expression"],
            [{"TableName": "Orders", "Expression": _SECRET_M}],
        )
        schema = None
        dax_measures = None
        tables = ["Orders"]

    pbix_path = tmp_path / "Sales.pbix"
    pbix_path.write_bytes(b"pbix")
    monkeypatch.setitem(
        sys.modules, "pbixray", types.SimpleNamespace(PBIXRay=lambda _path: _Model())
    )

    with caplog.at_level(logging.DEBUG):
        report = pbix_parser.parse_pbix_file(pbix_path)

    assert report.tables[0].source.source_type == "unknown"
    assert "hunter2" not in caplog.text
    assert "db.internal" not in caplog.text
    assert "table 'Orders': unknown source" in caplog.text
    assert "Custom.Connector" in caplog.text
    assert f"expression length: {len(_SECRET_M)}" in caplog.text


# S-08: one malformed report must not abort the whole scan.


def _pg_table(table_name, *, server="db.internal", database_name="warehouse",
              relation="sales.orders"):
    source = SourceInfo(
        source_type="postgresql",
        server=server,
        database=database_name,
        sql_table=relation,
        raw_expression=f"PostgreSQL.Database {server} {database_name} {relation}",
    )
    return ParsedTable(
        table_name=table_name, m_expression=source.raw_expression, source=source
    )


def _report(name, tables, **fields):
    return DiscoveredReport(name=name, tmdl_path=f"C:/{name}", tables=tables, **fields)


def _run_scan(monkeypatch, reports):
    """Reconcile in-memory report snapshots without follow-up modules."""
    monkeypatch.setattr(runner, "_backup_db", lambda: None)
    monkeypatch.setattr(runner, "walk_reports_root", lambda _root: reports)
    return runner.run_scan("unused", run_followups=False, run_followup_probe=False)


def test_unparseable_postgres_server_has_no_exact_identity():
    assert postgres_server_identity("[pg-prod]") == ""
    assert postgres_server_identity("[pg-prod]:5433") == ""
    assert postgres_server_identity("[::1]:5433") == "[::1]:5433"


def test_scan_survives_a_report_with_an_unparseable_postgres_server(
    audit_db, monkeypatch
):
    reports = [
        _report("Malformed", [_pg_table("Orders", server="[pg-prod]")]),
        _report("Healthy", [_pg_table("Orders")]),
    ]

    result = _run_scan(monkeypatch, reports)

    assert result["status"] == "completed"
    with database.get_db() as db:
        rows = {
            row["report"]: row
            for row in db.execute(
                """SELECT r.name AS report, s.name AS source, spi.source_id AS identified
                     FROM reports r
                     JOIN report_tables rt ON rt.report_id=r.id
                     JOIN sources s ON s.id=rt.source_id
                     LEFT JOIN source_postgres_identities spi ON spi.source_id=s.id"""
            ).fetchall()
        }
    assert rows["Healthy"]["source"] == "sales.orders"
    assert rows["Healthy"]["identified"] is not None
    assert rows["Malformed"]["source"].startswith("unresolved_pg_query_")
    assert rows["Malformed"]["identified"] is None


def _write_tmdl_report(root, name, *, expressions: bytes | None = None):
    definition = root / name / f"{name}.SemanticModel" / "Definition"
    (definition / "Tables").mkdir(parents=True)
    (definition / "Tables" / "Orders.tmdl").write_text(
        "table Orders\n\tcolumn Id\n", encoding="utf-8"
    )
    if expressions is not None:
        (definition / "expressions.tmdl").write_bytes(expressions)


def test_non_utf8_expressions_file_skips_only_that_report(tmp_path):
    _write_tmdl_report(tmp_path, "Broken", expressions=b"expression Server = \xff\xfe\xfa")
    _write_tmdl_report(tmp_path, "Healthy", expressions=b'expression Server = "db"\n')

    reports = {report.name: report for report in walker.walk_reports_root(tmp_path)}

    assert set(reports) == {"Broken", "Healthy"}
    assert reports["Healthy"].discovery["snapshot_complete"] is True
    assert reports["Healthy"].expressions == {"Server": "db"}
    assert reports["Broken"].discovery["snapshot_complete"] is False
    assert reports["Broken"].discovery["issues"] == ["expressions.tmdl could not be read"]


def test_unnamed_visuals_on_one_page_are_all_catalogued(audit_db, monkeypatch):
    query = {
        "From": [{"Name": "o", "Entity": "Orders"}],
        "Select": [{"Column": {
            "Expression": {"SourceRef": {"Source": "o"}}, "Property": "Amount",
        }}],
    }

    def container(name, visual_type):
        config = {"singleVisual": {"visualType": visual_type, "prototypeQuery": query}}
        if name is not None:
            config["name"] = name
        return {"config": json.dumps(config)}

    layout = layout_parser._parse_layout_json(
        {"sections": [{"displayName": "Overview", "visualContainers": [
            container(None, "card"),
            container("a1b2c3", "barChart"),
            container(None, "tableEx"),
            container("a1b2c3", "lineChart"),
        ]}]},
        "Sales.pbix",
    )
    # Confirms the defect's precondition: the parser yields repeated ids.
    assert [visual.visual_id for visual in layout.pages[0].visuals] == [
        "", "a1b2c3", "", "a1b2c3",
    ]

    result = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")], layout=layout)])

    assert result["status"] == "completed"
    with database.get_db() as db:
        visuals = db.execute(
            """SELECT rv.visual_id, rv.visual_type,
                      (SELECT COUNT(*) FROM visual_fields vf WHERE vf.visual_id=rv.id) AS fields
                 FROM report_visuals rv ORDER BY rv.id"""
        ).fetchall()
    assert [(row["visual_id"], row["visual_type"]) for row in visuals] == [
        ("unnamed", "card"),
        ("a1b2c3", "barChart"),
        ("unnamed (2)", "tableEx"),
        ("a1b2c3 (2)", "lineChart"),
    ]
    assert [row["fields"] for row in visuals] == [1, 1, 1, 1]


# S-05: calculated and "Enter data" tables are not broken references.

_ENTER_DATA_M = (
    'let Source = Table.FromRows(Json.Document(Binary.Decompress('
    'Binary.FromText("i45WMlTSUTJSitWJVjIGskyUYmMB", BinaryEncoding.Base64), '
    'Compression.Deflate)), let _t = ((type nullable text) meta '
    '[Serialized.Text = true]) in type table [Region = _t]) in Source'
)
_CALENDAR_M = "let Source = List.Dates(#date(2024, 1, 1), 366, #duration(1, 0, 0, 0)) in Source"


def _m_table(table_name, expression):
    return ParsedTable(
        table_name=table_name,
        m_expression=expression,
        source=_parse_m_expression(expression),
    )


def test_calculated_tables_are_not_broken_references(audit_db, monkeypatch):
    tables = [
        _pg_table("Orders"),
        _m_table("Regions", _ENTER_DATA_M),
        _m_table("Calendar", _CALENDAR_M),
    ]
    assert [table.source.source_type for table in tables[1:]] == ["calculated", "calculated"]
    with database.get_db() as db:
        report_id = db.execute(
            "INSERT INTO reports(name, archived) VALUES ('Sales', 0)"
        ).lastrowid
        # Left open by a scan that still counted the calculated tables.
        db.execute(
            """INSERT INTO actions(report_id, type, status, notes, fingerprint)
               VALUES (?, 'broken_ref', 'open',
                       '2 broken report reference(s): Regions -> Unknown Source; '
                       || 'Calendar -> Unknown Source', ?)""",
            (report_id, f"broken_ref:{report_id}"),
        )

    result = _run_scan(monkeypatch, [_report("Sales", tables)])

    assert result["status"] == "completed"
    assert result["components"]["core"]["broken_refs"] == 0
    with database.get_db() as db:
        actions = db.execute(
            "SELECT status FROM actions WHERE type='broken_ref'"
        ).fetchall()
        scan = db.execute(
            "SELECT broken_refs, log FROM scan_runs WHERE id=?", (result["scan_id"],)
        ).fetchone()
        linked = {
            row["table_name"]: row["source_id"]
            for row in db.execute("SELECT table_name, source_id FROM report_tables")
        }
    assert [row["status"] for row in actions] == ["resolved"]
    assert scan["broken_refs"] == 0
    assert "BROKEN:" not in scan["log"]
    assert linked["Orders"] is not None
    assert linked["Regions"] is None and linked["Calendar"] is None


def test_unmatched_real_source_is_still_a_broken_reference(audit_db, monkeypatch):
    # Guard for the fix above: only calculated tables stop counting.
    monkeypatch.setattr(runner, "deduplicate_sources", lambda _reports: {})

    result = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")])])

    assert result["components"]["core"]["broken_refs"] == 1
    with database.get_db() as db:
        action = db.execute(
            "SELECT status, notes FROM actions WHERE type='broken_ref'"
        ).fetchone()
    assert action["status"] == "open"
    assert "Orders -> sales.orders" in action["notes"]


# S-06: the pre-scan backup must be a consistent copy of a WAL-mode database.


def _timestamped_backups(backup_dir):
    return sorted(
        path.name for path in backup_dir.iterdir()
        if path.name.startswith("governance_2") and path.suffix == ".db"
    )


def test_backup_includes_commits_still_in_the_write_ahead_log(tmp_path, monkeypatch):
    db_path = tmp_path / "governance.db"
    monkeypatch.setattr(runner, "DB_PATH", str(db_path))
    with closing(sqlite3.connect(db_path)) as writer:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT)")
        writer.execute("INSERT INTO app_settings VALUES ('marker', 'committed')")
        writer.commit()
        # The committed row lives only in the -wal file while the app is up.
        assert (tmp_path / "governance.db-wal").stat().st_size > 0

        runner._backup_db()

        backup_dir = tmp_path / "backups"
        created = [path.name for path in backup_dir.iterdir()]
        assert created == _timestamped_backups(backup_dir) and len(created) == 1
        with closing(sqlite3.connect(backup_dir / created[0])) as backup:
            assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert backup.execute(
                "SELECT value FROM app_settings WHERE key='marker'"
            ).fetchone()[0] == "committed"


def test_backup_finishes_while_another_connection_keeps_writing(tmp_path, monkeypatch):
    db_path = tmp_path / "governance.db"
    monkeypatch.setattr(runner, "DB_PATH", str(db_path))
    with closing(sqlite3.connect(db_path)) as setup:
        setup.execute("PRAGMA journal_mode = WAL")
        setup.execute("CREATE TABLE payload(id INTEGER PRIMARY KEY, body BLOB)")
        setup.execute("CREATE TABLE heartbeat(id INTEGER PRIMARY KEY, beat INTEGER)")
        setup.execute("INSERT INTO heartbeat VALUES (1, 0)")
        # Several megabytes: far more pages than one paged backup step copies.
        setup.executemany(
            "INSERT INTO payload(body) VALUES (?)", [(bytes(4096),)] * 1500
        )
        setup.commit()

    stop = threading.Event()

    def keep_writing():
        # Like workers and schedulers reporting progress during a scan.
        with closing(sqlite3.connect(db_path, timeout=30)) as writer:
            while not stop.is_set():
                writer.execute("UPDATE heartbeat SET beat = beat + 1 WHERE id = 1")
                writer.commit()

    writer_thread = threading.Thread(target=keep_writing, daemon=True)
    backup_thread = threading.Thread(target=runner._backup_db, daemon=True)
    writer_thread.start()
    try:
        backup_thread.start()
        backup_thread.join(60)
        still_running = backup_thread.is_alive()
    finally:
        stop.set()
        writer_thread.join(30)

    assert not still_running, "the backup never finished under concurrent writes"
    [backup_path] = (tmp_path / "backups").glob("governance_*.db")
    with closing(sqlite3.connect(backup_path)) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM payload").fetchone()[0] == 1500


def _database_with_old_backups(tmp_path, monkeypatch):
    db_path = tmp_path / "governance.db"
    monkeypatch.setattr(runner, "DB_PATH", str(db_path))
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT)")
        db.commit()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = [
        "governance_20200101_000000.db",
        "governance_20200102_000000.db",
        "governance_20200103_000000.db",
    ]
    kept_by_hand = ["governance_before_migration.db", "notes.txt"]
    for name in old + kept_by_hand:
        (backup_dir / name).write_bytes(b"old")
    return backup_dir, old, kept_by_hand


def test_backup_keeps_every_earlier_backup_by_default(tmp_path, monkeypatch):
    backup_dir, old, kept_by_hand = _database_with_old_backups(tmp_path, monkeypatch)

    runner._backup_db()

    timestamped = _timestamped_backups(backup_dir)
    assert timestamped[:3] == old and len(timestamped) == 4
    assert all((backup_dir / name).exists() for name in kept_by_hand)


def test_backup_prunes_to_the_newest_when_keep_is_given(tmp_path, monkeypatch):
    backup_dir, old, kept_by_hand = _database_with_old_backups(tmp_path, monkeypatch)

    runner._backup_db(keep=2)

    timestamped = _timestamped_backups(backup_dir)
    assert len(timestamped) == 2
    assert timestamped[0] == old[-1]
    assert timestamped[1] > old[-1]
    # Only the app's own timestamped backups are ever pruned.
    assert all((backup_dir / name).exists() for name in kept_by_hand)

    # A misconfigured zero can never delete the backup that was just written.
    runner._backup_db(keep=0)

    assert len(_timestamped_backups(backup_dir)) == 1


def test_failed_backup_never_prunes_earlier_backups(tmp_path, monkeypatch):
    backup_dir, old, _kept_by_hand = _database_with_old_backups(tmp_path, monkeypatch)
    (tmp_path / "governance.db").write_bytes(b"not a database" * 64)

    runner._backup_db(keep=1)

    assert _timestamped_backups(backup_dir) == old
    assert not [path for path in backup_dir.iterdir() if path.suffix == ".tmp"]


def test_scan_backups_are_ignored_by_git():
    ignore_file = Path(__file__).resolve().parents[1] / ".gitignore"

    assert "backups/" in ignore_file.read_text(encoding="utf-8").splitlines()


# S-19: scan-run lookup, host-only folder opening, removing the last measure.

COLLEAGUE = ("203.0.113.9", 50000)


def test_unknown_scan_run_is_a_404(audit_db):
    client = TestClient(main.app)
    with database.get_db() as db:
        scan_id = db.execute(
            "INSERT INTO scan_runs(started_at, status) VALUES ('2026-11-01T00:00:00+00:00', 'completed')"
        ).lastrowid

    missing = client.get(f"/api/scanner/runs/{scan_id + 1}")
    found = client.get(f"/api/scanner/runs/{scan_id}")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Scan run not found"}
    assert found.status_code == 200
    assert found.json()["id"] == scan_id


def test_only_the_host_pc_can_open_a_folder(audit_db, monkeypatch, tmp_path):
    monkeypatch.setattr(user_identity, "resolve_hostname", lambda ip: None)
    user_identity.clear_cache()
    launched = []
    monkeypatch.setattr(
        scanner_router.subprocess, "Popen", lambda command: launched.append(command)
    )
    probed = []
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self, **kwargs: probed.append(str(self)) or real_is_file(self, **kwargs),
    )
    body = {"path": str(tmp_path)}

    colleague = TestClient(main.app, client=COLLEAGUE).post("/api/scanner/open-path", json=body)

    assert colleague.status_code == 403
    assert colleague.json() == {
        "detail": "Only the host PC can open folders. On the host PC, open Metronome as http://localhost:8000."}
    assert launched == []
    assert str(tmp_path) not in probed

    host = TestClient(main.app).post("/api/scanner/open-path", json=body)

    assert host.status_code == 200
    assert host.json() == {"status": "ok", "opened": str(tmp_path)}
    assert len(launched) == 1 and launched[0][-1] == str(tmp_path)


def _stored_measures():
    with database.get_db() as db:
        return [
            row["measure_name"]
            for row in db.execute(
                "SELECT measure_name FROM report_measures ORDER BY measure_name"
            )
        ]


def _measures(*names):
    return [MeasureInfo("Orders", name, "SUM(Orders[Amount])") for name in names]


def test_scan_removes_a_reports_last_measure(audit_db, monkeypatch):
    _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")], measures=_measures("Total", "Margin"))])
    assert _stored_measures() == ["Margin", "Total"]

    _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")], measures=_measures("Total"))])
    assert _stored_measures() == ["Total"]

    result = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")])])

    assert result["status"] == "completed"
    assert _stored_measures() == []


def test_scan_keeps_measures_when_the_pbix_measure_list_was_unreadable(
    audit_db, monkeypatch, tmp_path
):
    _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")], measures=_measures("Total"))])

    class _Model:
        power_query = None
        schema = None
        tables = ["Orders"]

        @property
        def dax_measures(self):
            raise RuntimeError("measure metadata unavailable")

    pbix_path = tmp_path / "Sales.pbix"
    pbix_path.write_bytes(b"pbix")
    monkeypatch.setitem(
        sys.modules, "pbixray", types.SimpleNamespace(PBIXRay=lambda _path: _Model())
    )
    [unreadable] = walker._walk_pbix([pbix_path])
    assert unreadable.measures == []
    assert unreadable.discovery["snapshot_complete"] is True
    assert unreadable.discovery["measures_complete"] is False

    result = _run_scan(monkeypatch, [unreadable])

    assert result["status"] == "completed"
    assert _stored_measures() == ["Total"]


# S-18: a live-connection .pbix has a layout but no embedded data model.


def _write_pbix(path, *, with_data_model):
    query = {
        "From": [{"Name": "s", "Entity": "Sales"}],
        "Select": [{"Measure": {
            "Expression": {"SourceRef": {"Source": "s"}}, "Property": "Revenue",
        }}],
    }
    config = {"name": "v1", "singleVisual": {"visualType": "card", "prototypeQuery": query}}
    layout = {"sections": [{
        "displayName": "Overview",
        "visualContainers": [{"config": json.dumps(config)}],
    }]}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Report/Layout", json.dumps(layout).encode("utf-16-le"))
        archive.writestr("Connections", "{}")
        if with_data_model:
            archive.writestr("DataModel", b"unreadable model")


def _pbixray_without_a_model(monkeypatch):
    def refuse(_path):
        raise RuntimeError("No supported data model file found.")

    monkeypatch.setitem(sys.modules, "pbixray", types.SimpleNamespace(PBIXRay=refuse))


def test_live_connection_pbix_is_catalogued_from_its_layout(
    audit_db, monkeypatch, tmp_path
):
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    _write_pbix(reports_root / "Live Sales.pbix", with_data_model=False)
    _pbixray_without_a_model(monkeypatch)
    monkeypatch.setattr(runner, "_backup_db", lambda: None)

    first = runner.run_scan(str(reports_root), run_followups=False, run_followup_probe=False)
    second = runner.run_scan(str(reports_root), run_followups=False, run_followup_probe=False)

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["reports_preserved_incomplete"] == 0
    with database.get_db() as db:
        report = db.execute("SELECT id, name FROM reports").fetchall()
        tables = db.execute("SELECT COUNT(*) FROM report_tables").fetchone()[0]
        visuals = db.execute(
            """SELECT rp.page_name, rv.visual_id, vf.table_name, vf.field_name
                 FROM report_pages rp
                 JOIN report_visuals rv ON rv.page_id=rp.id
                 JOIN visual_fields vf ON vf.visual_id=rv.id"""
        ).fetchall()
    assert [row["name"] for row in report] == ["Live Sales"]
    assert tables == 0
    assert [tuple(row) for row in visuals] == [("Overview", "v1", "Sales", "Revenue")]


def test_live_connection_pbix_never_replaces_its_tmdl_model(monkeypatch, tmp_path):
    _write_tmdl_report(tmp_path, "Sales")
    table_file = tmp_path / "Sales" / "Sales.SemanticModel" / "Definition" / "Tables" / "Orders.tmdl"
    pbix_path = tmp_path / "Sales.pbix"
    _write_pbix(pbix_path, with_data_model=False)
    os.utime(table_file, (100, 100))
    os.utime(pbix_path, (200, 200))  # the thin report is the newer snapshot
    _pbixray_without_a_model(monkeypatch)

    [report] = walker.walk_reports_root(tmp_path)

    assert [table.table_name for table in report.tables] == ["Orders"]
    assert [page.page_name for page in report.layout.pages] == ["Overview"]
    assert report.discovery["model_provider"] == "tmdl"
    assert report.discovery["snapshot_complete"] is True
    assert report.discovery["table_sets_disagree"] is False
    assert report.discovery["provider_warnings"] == []


def test_pbix_with_an_unreadable_data_model_stays_unparsed(monkeypatch, tmp_path):
    # Only a missing model means "live connection"; a broken one is a failure.
    pbix_path = tmp_path / "Broken.pbix"
    _write_pbix(pbix_path, with_data_model=True)
    _pbixray_without_a_model(monkeypatch)

    assert pbix_parser.parse_pbix_file(pbix_path) is None
    [placeholder] = walker._walk_pbix([pbix_path])
    assert placeholder.discovery["snapshot_complete"] is False


# S-09: a long report walk must stay alive, stoppable and correctly notified.


def test_walker_reports_progress_before_each_report(monkeypatch, tmp_path):
    _write_tmdl_report(tmp_path, "Alpha")
    _write_tmdl_report(tmp_path, "Beta")
    _write_pbix(tmp_path / "Gamma.pbix", with_data_model=False)
    _pbixray_without_a_model(monkeypatch)
    calls = []

    with_callback = walker.walk_reports_root(
        tmp_path, on_report=lambda position, total: calls.append((position, total))
    )
    without_callback = walker.walk_reports_root(tmp_path)

    assert calls == [(1, 3), (2, 3), (3, 3)]
    assert sorted(report.name for report in with_callback) == ["Alpha", "Beta", "Gamma"]
    assert [report.name for report in without_callback] == [
        report.name for report in with_callback
    ]


def _long_walk(tmp_path, monkeypatch, during_parse):
    """Scan three TMDL reports, running ``during_parse`` inside each parse."""
    for name in ("Alpha", "Beta", "Gamma"):
        _write_tmdl_report(tmp_path, name)
    monkeypatch.setattr(runner, "_backup_db", lambda: None)
    job_id = jobs.create_job("full_scan")
    real_parse = walker._scan_tmdl_report_folder

    def parse(report_dir):
        during_parse(job_id, report_dir.name)
        return real_parse(report_dir)

    monkeypatch.setattr(walker, "_scan_tmdl_report_folder", parse)
    return runner.run_scan(
        str(tmp_path), operation_id=job_id, run_followups=False, run_followup_probe=False
    )


def test_discovery_heartbeats_once_per_report(audit_db, monkeypatch, tmp_path):
    seen_at_parse_start = []

    def eleven_quiet_minutes(job_id, _name):
        seen_at_parse_start.append((
            jobs.get_job(job_id)["is_stale"],
            [run["is_stalled"] for run in modules.runs_for_job(job_id)],
        ))
        quiet_since = (
            datetime.now(timezone.utc) - timedelta(seconds=jobs.STALE_AFTER_SECONDS + 60)
        ).isoformat(timespec="seconds")
        with database.get_db() as db:
            db.execute("UPDATE scanner_jobs SET heartbeat_at=? WHERE id=?", (quiet_since, job_id))
            db.execute(
                "UPDATE scanner_module_runs SET heartbeat_at=? WHERE scanner_job_id=?",
                (quiet_since, job_id),
            )

    result = _long_walk(tmp_path, monkeypatch, eleven_quiet_minutes)

    assert result["status"] == "completed"
    # Each report starts from a fresh heartbeat, however long the last one took.
    assert seen_at_parse_start == [(False, [False])] * 3


def test_stop_during_discovery_takes_effect_before_the_next_report(
    audit_db, monkeypatch, tmp_path
):
    parsed = []

    def stop_while_parsing(job_id, name):
        parsed.append(name)
        control.request_stop_existing_work("Stop requested.", exclude_scanner_job_id=job_id)

    result = _long_walk(tmp_path, monkeypatch, stop_while_parsing)

    assert result["status"] == "stopped"
    assert parsed == ["Alpha"]
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_module_stall_threshold_follows_the_scanner_setting():
    probe = (
        "from app.scanner import jobs, modules; "
        "print(jobs.STALE_AFTER_SECONDS, modules.STALE_AFTER_SECONDS)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DG_SCANNER_STALE_AFTER_SECONDS": "120"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["120", "120"]


@pytest.fixture
def outlook(audit_db, monkeypatch):
    """Record scanner emails instead of handing them to Outlook."""
    from app.routers import email

    subjects = []

    def launch(messages, mode, purpose):
        with database.get_db() as db:
            dispatch_id = db.execute(
                """INSERT INTO outlook_dispatches
                       (purpose, task_name, payload_path, receipt_path, status, message_count)
                   VALUES (?, ?, 'payload', 'receipt', 'pending', 1)""",
                (purpose, f"scanner-test-{len(subjects)}"),
            ).lastrowid
        subjects.append(messages[0]["subject"])
        return {"id": int(dispatch_id), "status": "pending"}

    monkeypatch.setattr(email, "launch_outlook_dispatch", launch)
    notifications.save_notification_settings(["ops@example.com"])
    return subjects


def _stalled_then_failed_run(module_key, job_type):
    job_id = jobs.create_job(job_type)
    run_id = modules.create_module_run(module_key, scanner_job_id=job_id)
    quiet_since = (
        datetime.now(timezone.utc) - timedelta(seconds=modules.STALE_AFTER_SECONDS + 60)
    ).isoformat(timespec="seconds")
    with database.get_db() as db:
        db.execute(
            "UPDATE scanner_module_runs SET heartbeat_at=? WHERE id=?", (quiet_since, run_id)
        )
    assert notifications.notify_stalled_module_runs()["stalled"] == 1
    modules.finish_module_run(
        run_id,
        status="failed",
        summary="Report discovery failed.",
        details={"status": "failed", "error": "report root unavailable"},
    )
    return job_id, run_id


def test_full_scan_failure_email_follows_a_stall_email_once(outlook):
    job_id, run_id = _stalled_then_failed_run("report_catalog", "full_scan")

    first = notifications.notify_full_refresh_failures(job_id)
    second = notifications.notify_full_refresh_failures(job_id)

    assert outlook == ["Metronome: Scanner work stalled", "Metronome: Scanner module failure"]
    assert first["status"] == "pending"
    assert second == {"status": "not_applicable", "dispatch_id": None}
    assert modules.get_module_run(run_id)["notification_dispatch_id"] == first["dispatch_id"]
    assert notifications.notify_stalled_module_runs()["stalled"] == 0


def test_standalone_failure_email_follows_a_stall_email_once(outlook):
    _job_id, run_id = _stalled_then_failed_run("source_freshness", "source_freshness")

    first = notifications.notify_standalone_failure(run_id)
    second = notifications.notify_standalone_failure(run_id)

    assert outlook == ["Metronome: Scanner work stalled", "Metronome: Scanner module failure"]
    assert first["status"] == "pending"
    assert second == {"status": "not_applicable", "dispatch_id": None}


# S-01: archiving and restoring a PostgreSQL source must never break scans.

NOW = "2026-11-01T00:00:00+00:00"


def _operator():
    return types.SimpleNamespace(state=types.SimpleNamespace(actor="Operator"))


def _orders_sources():
    """Every source row holding the warehouse sales.orders identity."""
    with database.get_db() as db:
        return [
            (int(row["source_id"]), bool(row["archived"]))
            for row in exact_identity_rows(
                db,
                server="db.internal",
                database="warehouse",
                schema="sales",
                relation="orders",
                include_archived=True,
            )
        ]


def _linked_source(table_name):
    with database.get_db() as db:
        return db.execute(
            "SELECT source_id FROM report_tables WHERE table_name=?", (table_name,)
        ).fetchone()["source_id"]


def _two_sources_with_one_identity(*, first_archived):
    with database.get_db() as db:
        for source_id, name, archived in (
            (1, "sales.orders", int(first_archived)),
            (2, "sales.orders [warehouse@db.internal]", 0),
        ):
            db.execute(
                """INSERT INTO sources(id, name, type, discovered_by, archived)
                   VALUES (?, ?, 'postgresql', 'scan', ?)""",
                (source_id, name, archived),
            )
            claim = upsert_postgres_identity(
                db,
                source_id=source_id,
                server="db.internal",
                database="warehouse",
                schema="sales",
                relation="orders",
                verified_at=NOW,
            )
            assert claim["status"] == "claimed"


def test_archive_then_restore_of_a_postgres_source_keeps_every_scan_working(
    audit_db, monkeypatch
):
    budget = SourceInfo(
        source_type="excel",
        file_path="//share/finance/budget.xlsx",
        raw_expression='Excel.Workbook(File.Contents("//share/finance/budget.xlsx"))',
    )
    reports = [_report("Sales", [
        _pg_table("Orders"),
        ParsedTable(table_name="Budget", m_expression=budget.raw_expression, source=budget),
    ])]

    first = _run_scan(monkeypatch, reports)
    [(orders_id, _archived)] = _orders_sources()
    budget_id = _linked_source("Budget")
    assert _linked_source("Orders") == orders_id

    # The operator archives two sources the report still reads.
    for source_id in (orders_id, budget_id):
        assert archive.toggle_archive("source", source_id, _operator())["archived"] is True

    second = _run_scan(monkeypatch, reports)

    # No twin: the PostgreSQL row is reused and stays archived, exactly like
    # the archived Excel row a report still references.
    assert _orders_sources() == [(orders_id, True)]
    assert (_linked_source("Orders"), _linked_source("Budget")) == (orders_id, budget_id)
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
        assert db.execute(
            "SELECT archived FROM sources WHERE id=?", (budget_id,)
        ).fetchone()["archived"] == 1

    assert archive.toggle_archive("source", orders_id, _operator())["archived"] is False

    third = _run_scan(monkeypatch, reports)
    fourth = _run_scan(monkeypatch, reports)

    assert [scan["status"] for scan in (first, second, third, fourth)] == ["completed"] * 4
    assert [scan["new_sources"] for scan in (first, second, third, fourth)] == [2, 0, 0, 0]
    assert _orders_sources() == [(orders_id, False)]
    assert _linked_source("Orders") == orders_id


def test_restoring_a_source_whose_identity_is_already_active_is_refused(audit_db):
    _two_sources_with_one_identity(first_archived=True)

    with pytest.raises(HTTPException) as refused:
        archive.toggle_archive("source", 1, _operator())

    assert refused.value.status_code == 409
    assert '"sales.orders [warehouse@db.internal]" (id 2)' in refused.value.detail
    assert _orders_sources() == [(1, True), (2, False)]

    # Once the operator archives the other row, the original can come back.
    assert archive.toggle_archive("source", 2, _operator())["archived"] is True
    assert archive.toggle_archive("source", 1, _operator())["archived"] is False
    assert _orders_sources() == [(1, False), (2, True)]


def test_restore_refusal_reaches_the_browser_as_a_409(audit_db):
    _two_sources_with_one_identity(first_archived=True)

    response = TestClient(main.app).post("/api/archive/source/1")

    assert response.status_code == 409
    assert "sales.orders [warehouse@db.internal]" in response.json()["detail"]
    assert _orders_sources() == [(1, True), (2, False)]


def test_scan_uses_the_lowest_source_when_two_active_rows_share_an_identity(
    audit_db, monkeypatch, caplog
):
    _two_sources_with_one_identity(first_archived=False)

    with caplog.at_level(logging.WARNING, logger="app.scanner.runner"):
        first = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")])])
        second = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")])])

    assert (first["status"], second["status"]) == ("completed", "completed")
    assert _linked_source("Orders") == 1
    assert _orders_sources() == [(1, False), (2, False)]
    warnings = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
        and "Ambiguous PostgreSQL identity" in record.getMessage()
    ]
    assert len(warnings) == 2
    assert "sources 1, 2; using source 1" in warnings[0]
    with database.get_db() as db:
        log = db.execute(
            "SELECT log FROM scan_runs WHERE id=?", (second["scan_id"],)
        ).fetchone()["log"]
    assert "sources 1, 2; using source 1" in log


def test_an_active_source_wins_over_an_archived_twin(audit_db, monkeypatch):
    # The state an older version leaves behind: archived original, active twin.
    _two_sources_with_one_identity(first_archived=True)

    result = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")])])

    assert result["status"] == "completed"
    assert _linked_source("Orders") == 2
    assert _orders_sources() == [(1, True), (2, False)]


def test_core_scan_failure_still_rolls_back_and_is_redacted(audit_db, monkeypatch):
    # The ambiguous-identity abort used to cover this contract; keep it pinned.
    def fail(*_args, **_kwargs):
        raise RuntimeError("password=core-secret")

    monkeypatch.setattr(runner, "reconcile_all_flow_targets", fail)

    result = _run_scan(monkeypatch, [_report("Sales", [_pg_table("Orders")])])

    assert result["status"] == "failed"
    assert result["error"] == "Redacted; review server logs."
    assert result["components"]["core"]["status"] == "failed"
    assert result["components"]["core"]["error"] == "Redacted; review server logs."
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        scan = db.execute(
            "SELECT status, components_json, log FROM scan_runs WHERE id=?",
            (result["scan_id"],),
        ).fetchone()
    persisted_components = json.loads(scan["components_json"])
    assert scan["status"] == "failed"
    assert scan["log"] == "Core discovery failed; review server logs."
    assert persisted_components["core"]["status"] == "failed"
    assert persisted_components["core"]["error"] == "Redacted; review server logs."
