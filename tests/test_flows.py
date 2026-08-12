import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database
from app.routers import flows


@pytest.fixture()
def flow_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flows.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def _request(actor="Analyst"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _site():
    return flows.SiteWrite(
        name="Report portal",
        auth_url="https://reports.example.test/login",
        base_url="https://reports.example.test",
    )


def _asap_site():
    return flows.SiteWrite(
        name="ASAP",
        adapter="asap_portal",
        auth_url="https://portal.example.test/portal/login/app",
        base_url="https://portal.example.test",
        discovery_enabled=True,
        discovery_scope=["Mobile"],
        discovery_weekday="saturday",
        discovery_time="06:00",
    )


def _report(site_id):
    return flows.ReportWrite(
        site_id=site_id,
        name="Weekly movement",
        report_url="https://reports.example.test/report/weekly-movement",
        ready_text="Week",
        open_export_text="Export detail",
        download_text="Download CSV",
        filters=[
            flows.FilterWrite(
                filter_key="region",
                label="Region",
                control_label="Sell-in region",
                control_type="select",
                options=["Global", "North"],
                required=True,
            ),
            flows.FilterWrite(
                filter_key="week",
                label="Week",
                control_label="Week",
                control_type="week",
            ),
        ],
    )


def _asap_report(site_id):
    return flows.ReportWrite(
        site_id=site_id,
        name="Installed Base MENA",
        report_url="https://portal.example.test/portal/login/app",
        ready_text="Export Wizard (Detail)",
        download_text="Export CSV",
        automation={
            "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
            "report_tab": "Export Wizard (Detail)",
            "export_selector": "button.report-export",
        },
        filters=[
            flows.FilterWrite(
                filter_key="data_configuration",
                label="Data configuration",
                control_label="Data Configuration",
                control_type="select",
                options=["MENA - Global - Global", "Global - Global - MENA", "Global - Global - CIS"],
                required=True,
            ),
            flows.FilterWrite(
                filter_key="week",
                label="Sell-out week",
                control_label="Sell-out Week",
                control_type="week",
            ),
        ],
    )


def _mark_discovered(report_id):
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET source_kind='discovered', stale=0, discovery_key=name WHERE id=?",
            (report_id,),
        )


def _flow(site_id, report_id, **overrides):
    data = {
        "name": "Weekly report download",
        "site_id": site_id,
        "report_id": report_id,
        "enabled": True,
        "selections": {"region": "Global"},
        "download_mode": "one_per_week",
        "start_week": "2026-W30",
        "end_week": "2026-W32",
        "target_folder": r"C:\Reports\Downloads",
        "filename_template": "weekly_{week}.csv",
        "schedule_type": "weekly",
        "schedule_time": "08:00",
        "schedule_days": ["monday"],
        "sql_handoff_enabled": False,
    }
    data.update(overrides)
    return flows.FlowWrite(**data)


def _seed_catalog():
    site = flows.create_site(_site(), _request())
    report = flows.create_report(_report(site["id"]), _request())
    return site, report


def test_catalog_and_flow_configuration_persist_locally(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())

    assert saved["site_name"] == "Report portal"
    assert saved["report_name"] == "Weekly movement"
    assert saved["selections"] == {"region": "Global"}
    assert saved["sql_handoff_enabled"] is False
    catalog = flows.catalog()
    assert catalog["reports"][0]["filters"][0]["options"] == ["Global", "North"]


def test_report_filter_update_keeps_historical_definition_for_saved_runs(flow_db):
    site, report = _seed_catalog()
    updated = _report(site["id"])
    updated.filters = [updated.filters[0]]
    flows.update_report(report["id"], updated, _request())

    with database.get_db() as db:
        rows = db.execute(
            "SELECT filter_key, enabled FROM flow_report_filters WHERE report_id=? ORDER BY filter_key",
            (report["id"],),
        ).fetchall()
    assert [(row["filter_key"], row["enabled"]) for row in rows] == [("region", 1), ("week", 0)]


def test_one_per_week_job_is_expanded_without_delete_or_overwrite(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())

    assert queued["job"]["downloads"]["periods"] == ["2026-W30", "2026-W31", "2026-W32"]
    assert queued["job"]["downloads"]["collision_policy"] == "number_suffix"
    assert queued["job"]["downloads"]["delete_existing"] is False
    assert queued["job"]["downloads"]["overwrite_existing"] is False
    assert queued["job"]["execution"] == {
        "mode": "local", "host": "bi_desktop", "worker_id": "bi-desktop"
    }
    assert queued["job"]["sql_handoff"] == {"enabled": False, "status": "not_implemented"}


def test_asap_report_navigation_metadata_stays_local_and_enters_job(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"],
            report["id"],
            selections={"data_configuration": "MENA - Global - Global"},
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())

    assert report["automation"]["category_path"][-1] == "Installed Base (MENA)"
    assert queued["job"]["site"]["adapter"] == "asap_portal"
    assert queued["job"]["report"]["automation"]["report_tab"] == "Export Wizard (Detail)"


def test_asap_week_conversion_uses_portal_member_format():
    worker = __import__("app.flow_worker", fromlist=["_week_to_asap"])
    assert worker._week_to_asap("2026-W03") == "202603"
    with pytest.raises(RuntimeError, match="YYYY-Www"):
        worker._week_to_asap("202603")


def test_sql_handoff_cannot_be_enabled(flow_db):
    site, report = _seed_catalog()
    with pytest.raises(ValueError, match="later release"):
        _flow(site["id"], report["id"], sql_handoff_enabled=True)


def test_unknown_and_invalid_filter_values_are_rejected(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with pytest.raises(HTTPException, match="Unknown report filter"):
        flows.create_flow(
            _flow(site["id"], report["id"], selections={"region": "Global", "secret": "x"}),
            _request(),
        )
    with pytest.raises(HTTPException, match="Invalid Region"):
        flows.create_flow(
            _flow(site["id"], report["id"], selections={"region": "Unknown"}),
            _request(),
        )


def test_flow_rejects_manual_report_metadata(flow_db):
    site, report = _seed_catalog()
    with pytest.raises(HTTPException, match="discovered"):
        flows.create_flow(_flow(site["id"], report["id"]), _request())


def test_scan_discovery_upserts_and_marks_missing_stale_without_deleting(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base MENA",
        name="Installed Base MENA",
        report_url="https://portal.example.test",
        ready_text="Export Wizard",
        automation={"category_path": ["Mobile", "Installed Base", "Installed Base MENA"]},
        filters=[flows.DiscoveredFilter(
            filter_key="week", label="Sell-out Week", control_label="Sell-out Week",
            control_type="week", options=["202632"], position=0,
        )],
    )
    with database.get_db() as db:
        first = flows._apply_discovery(db, site["id"], [report], "2026-08-12T10:00:00")
        second = flows._apply_discovery(db, site["id"], [], "2026-08-19T10:00:00")
        row = db.execute("SELECT enabled, stale FROM flow_reports").fetchone()
    assert first["report_count"] == 1
    assert second["report_count"] == 0
    assert (row["enabled"], row["stale"]) == (0, 1)


def test_scan_discovery_keeps_duplicate_leaf_names_from_different_menu_paths(flow_db):
    site = flows.create_site(_asap_site(), _request())
    reports = [
        flows.DiscoveredReport(
            discovery_key=f"Mobile > {group} > Inflow Outflow",
            name="Inflow Outflow",
            report_url="https://portal.example.test",
            automation={"category_path": ["Mobile", group, "Inflow Outflow"]},
        )
        for group in ("Operations", "Inventory")
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(db, site["id"], reports, "2026-08-12T10:00:00")
        names = [row["name"] for row in db.execute(
            "SELECT name FROM flow_reports ORDER BY name"
        ).fetchall()]
    assert result["report_count"] == 2
    assert names == [
        "Mobile > Inventory > Inflow Outflow",
        "Mobile > Operations > Inflow Outflow",
    ]


def test_targeted_report_scan_queues_one_path_without_deleting_other_catalog_entries(flow_db, monkeypatch):
    site = flows.create_site(_asap_site(), _request())
    report = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base (MENA)",
        name="Installed Base (MENA)",
        report_url="https://portal.example.test",
        automation={"category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"]},
    )
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [report], "2026-08-12T10:00:00")
        report_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
    monkeypatch.setattr(flows, "launch_local_worker", lambda: {"status": "online"})
    queued = flows.queue_report_scan(report_id, _request())
    with database.get_db() as db:
        scan = db.execute("SELECT job_json FROM flow_catalog_scans WHERE id=?", (queued["id"],)).fetchone()
    job = json.loads(scan["job_json"])
    assert job["discovery"]["report_paths"] == [["Mobile", "Installed Base", "Installed Base (MENA)"]]
    assert job["discovery"]["delete_missing"] is False


def test_scan_estimate_uses_recorded_median(flow_db):
    site = flows.create_site(_asap_site(), _request())
    with database.get_db() as db:
        flows._store_timings(db, [{"phase": "total", "duration_ms": 80_000}], operation_type="catalog_scan", site_id=site["id"])
        flows._store_timings(db, [{"phase": "total", "duration_ms": 100_000}], operation_type="catalog_scan", site_id=site["id"])
    estimate = flows.operation_estimates(site_id=site["id"])["catalog_scan"]
    assert estimate["estimated_ms"] == 100_000
    assert estimate["sample_count"] == 2


def test_worker_claim_and_completion_records_artifact(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    worker = flows.WorkerRegister(
        worker_id="personal-session",
        display_name="Authenticated browser",
        capabilities={"adapters": ["web_export"]},
    )
    flows.register_worker(worker)
    claimed = flows.claim_run(worker.worker_id)
    assert claimed["run"]["id"] == queued["id"]

    flows.update_run(
        worker.worker_id,
        queued["id"],
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "Saved 1 CSV file."},
            artifacts=[{
                "period_key": "2026-W30",
                "file_path": r"C:\Reports\Downloads\weekly_2026-W30.csv",
                "filename": "weekly_2026-W30.csv",
                "file_size": 123,
                "checksum": "abc",
                "row_count": 5,
                "status": "saved",
            }],
            timings=[
                {"phase": "navigation", "duration_ms": 1200},
                {"phase": "total", "duration_ms": 2400, "item_count": 1},
            ],
        ),
    )
    with database.get_db() as db:
        run = db.execute("SELECT status FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()
        artifact = db.execute("SELECT * FROM flow_run_files WHERE run_id=?", (queued["id"],)).fetchone()
        timing = db.execute("SELECT duration_ms FROM flow_operation_timings WHERE run_id=? AND phase='total'", (queued["id"],)).fetchone()
    assert run["status"] == "succeeded"
    assert artifact["filename"] == "weekly_2026-W30.csv"
    assert timing["duration_ms"] == 2400


def test_safe_output_path_never_overwrites(tmp_path):
    existing = tmp_path / "report.csv"
    existing.write_text("original")
    output = __import__("app.flow_worker", fromlist=["_safe_output_path"])._safe_output_path(tmp_path, "report.csv")
    assert output.name == "report (2).csv"
    assert existing.read_text() == "original"


def test_worker_source_contains_no_delete_or_overwrite_operation():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    forbidden = [".unlink(", ".rmdir(", "shutil.rmtree", "os.remove(", "os.unlink("]
    assert all(token not in source for token in forbidden)
    assert "_safe_output_path" in source


def test_database_schema_has_no_flow_delete_policy(flow_db):
    with database.get_db() as db:
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(flows)").fetchall()}
    assert "delete_existing" not in job_columns
    assert "cleanup_policy" not in job_columns


def test_database_migrates_existing_flow_catalog_before_discovery_index(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    import sqlite3
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE flow_sites (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, adapter TEXT NOT NULL DEFAULT 'web_export', base_url TEXT, auth_url TEXT, enabled INTEGER DEFAULT 1, created_at DATETIME, updated_at DATETIME);
            CREATE TABLE flow_reports (id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, name TEXT NOT NULL, report_url TEXT NOT NULL, ready_text TEXT, open_export_text TEXT, download_text TEXT, automation_json TEXT NOT NULL DEFAULT '{}', notes TEXT, enabled INTEGER DEFAULT 1, created_at DATETIME, updated_at DATETIME, UNIQUE(site_id, name));
        """)
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(flow_reports)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(flow_reports)")}
    assert "discovery_key" in columns
    assert "idx_flow_reports_discovery_key" in indexes


def test_database_upgrades_legacy_asap_site_adapter(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-asap.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sites (name, adapter, auth_url)
               VALUES ('ASAP', 'web_export', 'https://asap.sec.samsung.net/portal/login')"""
        )

    database.init_db()

    with database.get_db() as db:
        row = db.execute("SELECT adapter FROM flow_sites WHERE name='ASAP'").fetchone()
    assert row["adapter"] == "asap_portal"


def test_windows_worker_launcher_uses_direct_script_for_embedded_python():
    source = Path(__file__).parents[1].joinpath("tools", "run_flow_worker.ps1").read_text()
    assert '(Join-Path $CodeDir "app\\flow_worker.py")' in source


def test_setup_installs_headless_flow_worker_service():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert '$FlowServiceName = "MXFlowsWorker"' in source
    assert "install $FlowServiceName $PyExe" in source
    assert "start $FlowServiceName" in source
    assert "--name BI-desktop" in source
    assert "flow_worker_error.log" in source
    assert "$WorkerStartedAt = Get-Date" in source
    assert "$WorkerStartedAt.AddSeconds(-5)" in source
    assert '/api/flows/workers' in source
    assert "Flows worker registered with Metronome." in source


def test_setup_does_not_require_visible_asap_login_without_encrypted_credential():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert '$FlowCredentialPath = Join-Path $FlowProfile ".asap_credentials"' in source
    assert "(Test-Path $FlowCredentialPath)" in source
    assert "ASAP automatic sign-in is not configured yet." in source


def test_setup_bounds_stale_port_process_cleanup():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert "$KillProcess.WaitForExit(10000)" in source
    assert "Timed out waiting for taskkill" in source


def test_setup_merges_new_nested_files_without_purging_local_files():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    command = next(line for line in source.splitlines() if "& robocopy.exe" in line)
    assert "robocopy.exe $Inner.FullName $CodeDir /E" in command
    assert "/MIR" not in command
    assert "/PURGE" not in command


def test_worker_launcher_appends_diagnostic_log():
    source = Path(__file__).parents[1].joinpath("tools", "run_flow_worker.ps1").read_text()
    assert 'Start-Transcript -Path $WorkerLog -Append' in source
    assert '"flow_worker.log"' in source


def test_service_starts_headless_worker_service_instead_of_child_process():
    source = Path(__file__).parents[1].joinpath("app", "flow_local_runner.py").read_text()
    assert '["sc.exe", "start", SERVICE_NAME]' in source
    assert '"mode": "windows_service"' in source
    assert "subprocess.Popen" not in source


def test_worker_retries_registration_and_prevents_duplicates():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert "for attempt in range(60)" in source
    assert "_exclusive_worker_lock" in source
    assert "Another Metronome flow worker is already running." in source


def test_catalog_monitor_reports_worker_and_auto_refreshes():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "No BI desktop worker online" in source
    assert "Waiting for BI desktop worker to start." in source
    assert "_flowScheduleCatalogMonitor" in source


def test_due_scheduler_queues_once_and_advances_next_run(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "launch_local_worker", lambda: {"status": "launched", "mode": "local"})
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    with database.get_db() as db:
        db.execute("UPDATE flows SET next_run_at='2020-01-01T08:00:00' WHERE id=?", (saved["id"],))

    first = flows.queue_due_flows()
    second = flows.queue_due_flows()

    assert first["count"] == 1
    assert second["count"] == 0
    with database.get_db() as db:
        row = db.execute("SELECT next_run_at FROM flows WHERE id=?", (saved["id"],)).fetchone()
    assert row["next_run_at"] > "2020-01-01T08:00:00"


def test_manual_run_launches_bi_desktop_worker(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    launched = []
    monkeypatch.setattr(
        flows,
        "launch_local_worker",
        lambda: launched.append("bi-desktop") or {"status": "launched", "mode": "local"},
    )

    queued = flows.queue_run(saved["id"], _request())

    assert launched == ["bi-desktop"]
    assert queued["worker"] == {"status": "launched", "mode": "local"}


def test_asap_region_triplet_select_is_named_data_configuration():
    from app.flow_worker import _normalize_asap_filter_label

    options = [
        "MENA - Global - Global",
        "Global - Global - MENA",
        "Global - Global - CIS",
    ]
    assert _normalize_asap_filter_label(options[1], "select", options) == "Data Configuration"
    assert _normalize_asap_filter_label("Region", "select", options) == "Region"
