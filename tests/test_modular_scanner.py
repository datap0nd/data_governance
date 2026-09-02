import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database
from app.routers import scanner
from app.scanner import jobs, modules, notifications, pbix_parser, runner, walker
from app.scanner.tmdl_parser import ParsedTable


def _fresh_database(monkeypatch):
    temp_dir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(database, "DB_PATH", f"{temp_dir.name}/modular-scanner.db")
    database.init_db()
    return temp_dir


def _report(name, path, tables, *, layout=None):
    return walker.DiscoveredReport(
        name=name,
        tmdl_path=str(path),
        tables=[ParsedTable(table_name=table) for table in tables],
        layout=layout,
    )


def test_mixed_discovery_uses_newer_model_and_pbix_layout(monkeypatch, tmp_path):
    pbix_path = tmp_path / "Revenue.pbix"
    pbix_path.write_bytes(b"pbix")
    tmdl_path = tmp_path / "Revenue"
    table_dir = tmdl_path / "Revenue.SemanticModel" / "Definition" / "Tables"
    table_dir.mkdir(parents=True)
    table_file = table_dir / "Revenue.tmdl"
    table_file.write_text("table Revenue", encoding="utf-8")
    os.utime(pbix_path, (100, 100))
    os.utime(table_file, (200, 200))

    layout = object()
    monkeypatch.setattr(
        walker, "_walk_pbix", lambda paths: [_report("REVENUE", pbix_path, ["PbixOnly"], layout=layout)]
    )
    monkeypatch.setattr(
        walker, "_walk_tmdl", lambda root, report_dirs=None: [_report("Revenue", tmdl_path, ["TmdlOnly"])]
    )
    monkeypatch.setattr(walker, "_discover_pbix_files", lambda root: [pbix_path])
    monkeypatch.setattr(walker, "_discover_tmdl_report_dirs", lambda root: [tmdl_path])

    reports = walker.walk_reports_root(tmp_path)

    assert len(reports) == 1
    assert [table.table_name for table in reports[0].tables] == ["TmdlOnly"]
    assert reports[0].layout is layout
    assert reports[0].discovery["model_provider"] == "tmdl"
    assert reports[0].discovery["table_sets_disagree"] is True


def test_mixed_discovery_tie_prefers_tmdl(monkeypatch, tmp_path):
    pbix_path = tmp_path / "Sales.pbix"
    pbix_path.write_bytes(b"pbix")
    tmdl_path = tmp_path / "Sales"
    tmdl_path.mkdir()
    os.utime(pbix_path, (100, 100))
    os.utime(tmdl_path, (100, 100))
    pbix = _report("Sales", pbix_path, ["Same"])
    tmdl = _report("sales", tmdl_path, ["Same"])

    merged = walker._merge_report_discovery([pbix], [tmdl])

    assert merged == [tmdl]
    assert merged[0].discovery["model_provider"] == "tmdl"


def test_mixed_discovery_prefers_complete_provider_over_newer_incomplete(tmp_path):
    pbix_path = tmp_path / "Sales.pbix"
    pbix_path.write_bytes(b"pbix")
    tmdl_path = tmp_path / "Sales"
    tmdl_path.mkdir()
    os.utime(tmdl_path, (100, 100))
    os.utime(pbix_path, (200, 200))
    pbix = _report("Sales", pbix_path, ["Partial"])
    pbix.discovery = {
        "snapshot_complete": False,
        "issues": ["PBIX model table enumeration failed"],
    }
    tmdl = _report("Sales", tmdl_path, ["Trusted"])

    merged = walker._merge_report_discovery([pbix], [tmdl], root=tmp_path)

    assert merged == [tmdl]
    assert merged[0].discovery["snapshot_complete"] is True
    assert merged[0].discovery["model_provider"] == "tmdl"
    assert merged[0].discovery["provider_warnings"][0]["provider"] == "pbix"


def test_same_named_reports_at_different_paths_are_ambiguous(tmp_path):
    north_path = tmp_path / "North" / "Sales.pbix"
    south_path = tmp_path / "South" / "Sales.pbix"
    north_path.parent.mkdir()
    south_path.parent.mkdir()
    north_path.write_bytes(b"north")
    south_path.write_bytes(b"south")

    merged = walker._merge_report_discovery(
        [
            _report("Sales", north_path, ["North"]),
            _report("Sales", south_path, ["South"]),
        ],
        [],
        root=tmp_path,
    )

    assert len(merged) == 1
    assert merged[0].discovery["snapshot_complete"] is False
    assert merged[0].discovery["ambiguous_provider"] is True
    assert merged[0].discovery["candidate_count"] == 2
    assert merged[0].tables == []


def test_malformed_tmdl_table_marks_snapshot_incomplete(tmp_path):
    report_path = tmp_path / "Sales"
    table_path = report_path / "Sales.SemanticModel" / "Definition" / "Tables"
    table_path.mkdir(parents=True)
    (table_path / "Broken.tmdl").write_text("not a table", encoding="utf-8")

    report = walker._scan_tmdl_report_folder(report_path)

    assert report is not None
    assert report.discovery["snapshot_complete"] is False
    assert "Could not parse table file Broken.tmdl" in report.discovery["issues"]


def test_pbix_table_fallback_is_not_authoritative(monkeypatch, tmp_path):
    pbix_path = tmp_path / "Sales.pbix"
    pbix_path.write_bytes(b"pbix")

    class _Model:
        power_query = None
        schema = None
        dax_measures = None

        @property
        def tables(self):
            raise RuntimeError("model metadata unavailable")

    monkeypatch.setitem(sys.modules, "pbixray", types.SimpleNamespace(PBIXRay=lambda _path: _Model()))

    report = pbix_parser.parse_pbix_file(pbix_path)

    assert report is not None
    assert report.snapshot_complete is False
    assert report.parse_issues == ["PBIX model table enumeration failed"]


def test_unreadable_snapshot_metadata_is_incomplete(tmp_path):
    missing_path = tmp_path / "Missing.pbix"
    report = _report("Missing", missing_path, ["Partial"])

    merged = walker._merge_report_discovery([report], [], root=tmp_path)

    assert merged[0].discovery["snapshot_complete"] is False
    assert merged[0].discovery["model_modified_at"] is None


def test_discovery_depth_is_bounded(tmp_path):
    within = tmp_path / "one" / "two" / "three" / "four"
    beyond = within / "five"
    beyond.mkdir(parents=True)
    within_pbix = within / "Within.pbix"
    beyond_pbix = beyond / "Beyond.pbix"
    within_pbix.write_bytes(b"pbix")
    beyond_pbix.write_bytes(b"pbix")

    assert walker._discover_pbix_files(tmp_path) == [within_pbix]


def test_missing_report_root_fails_without_losing_catalog(monkeypatch, tmp_path):
    temp_dir = _fresh_database(monkeypatch)
    try:
        with database.get_db() as db:
            db.execute("INSERT INTO reports(name, tmdl_path) VALUES ('Last Good', 'saved')")
        monkeypatch.setattr(runner, "_backup_db", lambda: None)

        result = runner.run_scan(tmp_path / "missing", run_followups=False, run_followup_probe=False)

        assert result["status"] == "failed"
        with database.get_db() as db:
            names = [row["name"] for row in db.execute("SELECT name FROM reports").fetchall()]
        assert names == ["Last Good"]
        module_run = modules.list_module_runs("report_catalog", limit=1)[0]
        assert module_run["status"] == "failed"
        assert "does not exist" in (module_run["summary"] or "")
    finally:
        temp_dir.cleanup()


def test_module_runs_are_redacted_and_recovered(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        job_id = jobs.create_job("governance")
        run_id = modules.create_module_run("governance", scanner_job_id=job_id)
        modules.finish_module_run(
            run_id,
            status="failed",
            summary="password=topsecret failed",
            details={"status": "failed", "error": "token=topsecret"},
            log="postgresql://admin:topsecret@db/catalog",
        )
        stored = modules.get_module_run(run_id)
        assert "topsecret" not in str(stored)
        assert stored["status"] == "failed"

        interrupted = modules.create_module_run("source_freshness", scanner_job_id=job_id)
        assert modules.recover_interrupted_module_runs() == 1
        assert modules.get_module_run(interrupted)["status"] == "stopped"
    finally:
        temp_dir.cleanup()


def test_notification_recipients_normalize_and_disable(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        saved = notifications.save_notification_settings(
            [" Admin@example.com ", "admin@EXAMPLE.com", "ops@example.com"]
        )
        assert saved == {
            "recipients": ["Admin@example.com", "ops@example.com"],
            "enabled": True,
        }
        assert notifications.save_notification_settings([])["enabled"] is False
    finally:
        temp_dir.cleanup()


def test_stalled_notification_is_queued_once_and_reconciled(monkeypatch):
    from app.routers import email

    temp_dir = _fresh_database(monkeypatch)
    try:
        notifications.save_notification_settings(["ops@example.com"])
        job_id = jobs.create_job("source_freshness")
        run_id = modules.create_module_run("source_freshness", scanner_job_id=job_id)
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        with database.get_db() as db:
            db.execute(
                "UPDATE scanner_module_runs SET heartbeat_at=? WHERE id=?", (old, run_id)
            )

        def launch(messages, mode, purpose):
            with database.get_db() as db:
                cursor = db.execute(
                    """INSERT INTO outlook_dispatches
                           (purpose, task_name, payload_path, receipt_path, status, message_count)
                       VALUES (?, 'scanner-test', 'payload', 'receipt', 'pending', 1)""",
                    (purpose,),
                )
                dispatch_id = int(cursor.lastrowid)
            assert messages[0]["to"] == "ops@example.com"
            assert mode == "send"
            return {"id": dispatch_id, "status": "pending"}

        monkeypatch.setattr(email, "launch_outlook_dispatch", launch)

        first = notifications.notify_stalled_module_runs()
        second = notifications.notify_stalled_module_runs()
        assert first["stalled"] == 1
        assert second["stalled"] == 0
        stored = modules.get_module_run(run_id)
        assert stored["notification_status"] == "pending"
        modules.reconcile_notification_dispatch(
            stored["notification_dispatch_id"], "submitted", None
        )
        assert modules.get_module_run(run_id)["notification_status"] == "submitted"
    finally:
        temp_dir.cleanup()


def test_power_bi_failure_does_not_block_local_catalog(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    observed = []
    try:
        job_id = jobs.create_job("full_scan")
        monkeypatch.setattr(
            scanner,
            "trigger_pbi_sync_and_wait",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("workspace unavailable")
            ),
        )

        def local_scan(**kwargs):
            observed.append(kwargs["initial_components"])
            jobs.finish_job(job_id, status="completed_with_warnings", result={"status": "completed_with_warnings"})
            return {"status": "completed_with_warnings"}

        monkeypatch.setattr(scanner, "run_scan", local_scan)
        monkeypatch.setattr(scanner.scanner_notifications, "notify_full_refresh_failures", lambda _job_id: {})

        result = scanner._execute_full_scan_job(job_id, None, {})

        assert result["status"] == "completed_with_warnings"
        assert observed[0]["power_bi_metadata"]["status"] == "failed"
        assert modules.list_module_runs("power_bi_metadata", limit=1)[0]["status"] == "failed"
    finally:
        temp_dir.cleanup()


def test_all_seven_modules_have_standalone_workers():
    assert set(scanner._MODULE_WORKERS) == set(modules.MODULES_BY_KEY)
