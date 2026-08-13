import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database
from app import flow_worker
from app import main
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
            "export_text": "Export Wizard (Detail)",
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
        "download_mode": "one_per_period",
        "period_strategy": "fixed",
        "window_weeks": 1,
        "browser_mode": "headless",
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
    assert saved["transform_enabled"] is False
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


def test_one_per_period_job_is_expanded_without_delete_or_overwrite(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())

    assert queued["job"]["downloads"]["periods"] == [["2026-W30"], ["2026-W31"], ["2026-W32"]]
    assert queued["job"]["downloads"]["collision_policy"] == "number_suffix"
    assert queued["job"]["transformation"] == {
        "enabled": False, "script_path": None, "output_subfolder": "script_results",
        "input_argument": "--input", "output_argument": "--output",
    }
    assert queued["job"]["downloads"]["delete_existing"] is False
    assert queued["job"]["downloads"]["overwrite_existing"] is False
    assert queued["job"]["execution"] == {
        "mode": "local", "host": "bi_desktop", "browser_mode": "headless",
        "worker_id": "bi-desktop-headless",
    }
    assert queued["job"]["sql_handoff"] == {
        "enabled": False, "mode": None, "database": None, "schema": None, "table": None,
    }


def test_transformation_configuration_is_persisted_in_job(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(
        site["id"], report["id"], transform_enabled=True,
        transform_script_path=r"C:\Scripts\clean_report.py",
    ), _request())
    queued = flows.queue_run(saved["id"], _request())
    assert saved["transform_enabled"] is True
    assert queued["job"]["transformation"]["script_path"] == r"C:\Scripts\clean_report.py"


def test_transformation_requires_supported_absolute_script_path(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with pytest.raises(ValueError, match="absolute path"):
        _flow(site["id"], report["id"], transform_enabled=True, transform_script_path="clean.py")
    with pytest.raises(ValueError, match=".py, .ps1, or .exe"):
        _flow(site["id"], report["id"], transform_enabled=True, transform_script_path=r"C:\Scripts\clean.bat")


def test_single_csv_job_passes_the_whole_week_range_to_asap(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], download_mode="single",
            selections={"region": "Global"}, filename_template="range_{week}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())

    assert queued["job"]["downloads"]["periods"] == [["2026-W30", "2026-W31", "2026-W32"]]
    assert queued["job"]["downloads"]["delete_existing"] is False
    assert queued["job"]["downloads"]["overwrite_existing"] is False


def test_fixed_range_is_split_into_week_periods(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], download_mode="one_per_period",
            window_weeks=2, filename_template="period_{week}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["periods"] == [
        ["2026-W30", "2026-W31"], ["2026-W32"],
    ]
    assert queued["job"]["downloads"]["period_unit"] == "week"
    assert queued["job"]["downloads"]["period_size"] == 2


def test_start_to_latest_uses_newest_discovered_asap_week(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_report_filters SET options_json=? WHERE report_id=? AND control_type='week'",
            ('["202630","202631","202632","202633"]', report["id"]),
        )
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], period_strategy="latest", end_week=None,
            download_mode="one_per_period", window_weeks=2,
            filename_template="{flow}_{start_period}_{end_period}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["period_end_week"] == "2026-W33"
    assert queued["job"]["downloads"]["periods"] == [
        ["2026-W30", "2026-W31"], ["2026-W32", "2026-W33"],
    ]


def test_filename_uses_flow_start_end_and_selected_format():
    worker = __import__("app.flow_worker", fromlist=["_render_filename"])
    job = {
        "flow": {"name": "Inflow Outflow"},
        "report": {"name": "Movement"},
        "downloads": {"periods": [["2026-W19", "2026-W27"]], "file_format": "csv"},
    }
    filename = worker._render_filename(
        "{flow}_{start_period}_{end_period}.csv", job,
        ["2026-W19", "2026-W27"], 1,
    )
    assert filename == "Inflow_Outflow_W19_W27.csv"


def test_excel_format_is_rejected():
    with pytest.raises(ValueError, match="must be CSV"):
        flows.FlowWrite(
            name="Excel is not supported", site_id=1, report_id=1, enabled=False,
            selections={}, download_mode="single", period_strategy="fixed",
            file_format="xlsx", start_week="2026-W30", end_week="2026-W31",
            target_folder=r"C:\Reports", filename_template="report.xlsx",
        )


def test_rolling_window_advances_only_after_success(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], download_mode="single",
            period_strategy="rolling", window_weeks=3, end_week=None,
            filename_template="window_{week}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["periods"] == [["2026-W30", "2026-W31", "2026-W32"]]
    assert queued["job"]["downloads"]["next_start_week"] == "2026-W33"

    worker = flows.WorkerRegister(
        worker_id="rolling-worker", display_name="Rolling worker", capabilities={}
    )
    flows.register_worker(worker)
    claimed = flows.claim_run(worker.worker_id)
    flows.update_run(
        worker.worker_id, claimed["run"]["id"],
        flows.WorkerProgress(status="failed", error="test failure"),
    )
    assert flows.get_flow(saved["id"])["start_week"] == "2026-W30"

    retry = flows.queue_run(saved["id"], _request())
    claimed = flows.claim_run(worker.worker_id)
    flows.update_run(
        worker.worker_id, claimed["run"]["id"],
        flows.WorkerProgress(status="succeeded"),
    )
    assert flows.get_flow(saved["id"])["start_week"] == "2026-W33"


def test_week_prompt_can_be_supplied_by_range_instead_of_selection(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    _mark_discovered(report["id"])

    body = _flow(
        site["id"], report["id"], download_mode="single",
        selections={"data_configuration": "MENA - Global - Global"},
    )
    with database.get_db() as db:
        flows._validate_flow_selections(db, body)


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


def test_sql_handoff_requires_discovered_target(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    body = _flow(
        site["id"], report["id"], sql_handoff_enabled=True,
        sql_mode="append", sql_database="warehouse", sql_schema="reporting", sql_table="inflow",
    )
    with pytest.raises(HTTPException, match="latest SQL catalog"):
        flows.create_flow(body, _request())


def test_sql_handoff_target_is_persisted_without_executing_insert(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'inflow', CURRENT_TIMESTAMP, 0)"""
        )
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], sql_handoff_enabled=True,
            sql_mode="replace", sql_database="warehouse",
            sql_schema="reporting", sql_table="inflow",
        ),
        _request(),
    )
    job = flows.queue_run(saved["id"], _request())["job"]
    assert job["sql_handoff"] == {
        "enabled": True, "mode": "replace", "database": "warehouse",
        "schema": "reporting", "table": "inflow",
    }


def test_flow_activation_is_separate_from_editor(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"], enabled=False), _request())
    active = flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=True), _request())
    assert active["enabled"] is True
    assert active["next_run_at"] is not None
    paused = flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=False), _request())
    assert paused["enabled"] is False
    assert paused["next_run_at"] is None


def test_inactive_scheduled_flow_has_no_next_run(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"], enabled=False), _request())
    assert saved["schedule_type"] == "weekly"
    assert saved["next_run_at"] is None


def test_manual_flow_cannot_be_activated(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], enabled=False, schedule_type="manual",
            schedule_time=None, schedule_days=[],
        ),
        _request(),
    )
    with pytest.raises(HTTPException, match="daily or weekly"):
        flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=True), _request())


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
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "online"})
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


def test_asap_execution_uses_rendered_ui_not_internal_response_url():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert "expect_response" not in source
    assert "frame = _asap_wait_for_results(page)" in source
    assert '"stage": "report_execution"' in source
    assert '"stage": "file_export"' in source
    assert '"button.report-export"' not in source


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
    assert "--worker-id bi-desktop-headless" in source
    assert "--name BI-desktop-headless" in source
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


def test_setup_downloads_before_stopping_services_and_bounds_each_attempt():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    download = source.index("Invoke-WebRequestWithRetry -Uri $ZipUrl")
    stop = source.index("Stop-ScheduledTask -TaskName $HeadedFlowTaskName")
    assert download < stop
    assert "-TimeoutSec 60" in source


def test_setup_overlays_update_without_robocopy_or_purging_local_files():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert 'Copy-Item "$($Inner.FullName)\\*" $CodeDir -Recurse -Force' in source
    assert "robocopy" not in source.casefold()
    assert "/MIR" not in source
    assert "/PURGE" not in source
    assert not Path(__file__).parents[1].joinpath("tools", "nssm.exe").exists()
    assert Path(__file__).parents[1].joinpath("tools", "nssm.dist.exe").exists()
    assert '$NssmDistribution = "$CodeDir\\tools\\nssm.dist.exe"' in source
    assert 'Copy-Item $NssmDistribution $NssmExe -Force' in source


def test_update_endpoint_repairs_legacy_overlay_without_deleting_files(tmp_path):
    setup = tmp_path / "setup.ps1"
    legacy = 'if ($Inner) {\n    Copy-Item "$($Inner.FullName)\\*" $CodeDir -Recurse -Force\n}'
    setup.write_text(legacy)

    assert main._repair_legacy_setup_overlay(setup) is True
    repaired = setup.read_text()
    assert "Preserving installed tools\\nssm.exe" in repaired
    assert 'Move-Item $DownloadedNssm "$TempExtract\\nssm.exe.skipped" -Force' in repaired
    assert "Remove-Item" not in repaired
    assert main._repair_legacy_setup_overlay(setup) is False


def test_worker_launcher_appends_diagnostic_log():
    source = Path(__file__).parents[1].joinpath("tools", "run_flow_worker.ps1").read_text()
    assert 'Start-Transcript -Path $WorkerLog -Append' in source
    assert '"flow_worker_{0}.log"' in source


def test_service_starts_headless_worker_service_instead_of_child_process():
    source = Path(__file__).parents[1].joinpath("app", "flow_local_runner.py").read_text()
    assert '["sc.exe", "start", SERVICE_NAME]' in source
    assert '"System32", "schtasks.exe"' in source
    assert '[schtasks, "/Run", "/TN", HEADED_TASK_PATH]' in source
    assert 'HEADED_TASK_PATH = rf"\\{HEADED_TASK_NAME}"' in source
    assert "subprocess.Popen" not in source


def test_setup_registers_on_demand_interactive_headed_worker():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert '$HeadedFlowTaskName = "Metronome_Flows_Headed"' in source
    assert "New-ScheduledTaskPrincipal" in source
    assert "-LogonType Interactive" in source
    assert 'New-ScheduledTaskAction -Execute $PyExe' in source
    assert "--worker-id bi-desktop-headed" in source
    assert "--headed --idle-exit-seconds 60" in source
    assert ".metronome-flow-browser-headed" in source


def test_setup_stops_headed_worker_before_replacing_runtime_code():
    root = Path(__file__).parents[1]
    for filename in ("setup.ps1", "setup_ps1_clean.txt"):
        source = root.joinpath(filename).read_text()
        stop = "Stop-ScheduledTask -TaskName $HeadedFlowTaskName"
        assert stop in source
        assert source.index(stop) < source.index("Expand-Archive -Path $ZipPath")


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


def test_flow_list_does_not_require_builder_only_sql_controls():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'if (fields) fields.hidden = !enabled;' in source
    assert 'if ($("#flow-sql-fields")) updateSqlFields();' in source


def test_flow_builder_uses_discovered_week_dropdowns():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "function _flowDiscoveredWeeks" in source
    assert '<select id="flow-start-week" required>' in source
    assert '<select id="flow-end-week" required>' in source


def test_flow_ui_uses_list_activation_csv_only_and_expanded_logs():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "flow-enabled-switch" in source
    assert "Enable scheduled execution" not in source
    assert 'file_format: "csv"' in source
    assert 'id="flow-file-format"' not in source
    assert "Expanded logs" in source
    assert "/flow-runs/${run.id}" in source
    assert 'id="flow-transform-enabled"' in source
    assert 'id="flow-transform-browse"' in source
    assert "script_results" in source


def test_run_progress_events_and_traceback_are_persisted(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_runs SET worker_id='test-worker', status='claimed' WHERE id=?",
            (queued["id"],),
        )
    flows.update_run(
        "test-worker", queued["id"],
        flows.WorkerProgress(
            status="failed", progress={"stage": "sql_insertion", "message": "Insert failed"},
            error="wrong columns", traceback="Traceback: example",
        ),
    )
    detail = flows.get_run(queued["id"])
    assert detail["events"][0]["stage"] == "sql_insertion"
    assert detail["events"][0]["traceback"] == "Traceback: example"


def test_due_scheduler_queues_once_and_advances_next_run(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "launched", "mode": mode})
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
        lambda mode: launched.append(mode) or {"status": "launched", "mode": mode},
    )

    queued = flows.queue_run(saved["id"], _request())

    assert launched == ["headless"]
    assert queued["worker"] == {"status": "launched", "mode": "headless"}


def test_headed_flow_is_routed_only_to_headed_worker(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    launched = []
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode: launched.append(mode) or {"status": "launched", "mode": mode},
    )
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headless", display_name="Background", capabilities={"headed": False},
    ))
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible", capabilities={"headed": True},
    ))

    assert queued["job"]["execution"]["browser_mode"] == "headed"
    assert queued["job"]["execution"]["worker_id"] == "bi-desktop-headed"
    assert launched == ["headed"]
    assert flows.claim_run("bi-desktop-headless")["run"] is None
    assert flows.claim_run("bi-desktop-headed")["run"]["id"] == queued["id"]


def test_queued_run_can_retry_worker_launch_without_duplicate(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    launches = []
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode: launches.append(mode) or {"status": "starting", "mode": mode},
    )

    first = flows.queue_run(saved["id"], _request())
    second = flows.queue_run(saved["id"], _request())

    assert first["id"] == second["id"]
    assert first["resumed"] is False
    assert second["resumed"] is True
    assert launches == ["headed", "headed"]
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0] == 1


def test_stop_cancels_headed_run_and_targets_reported_worker_pid(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    stopped = []
    monkeypatch.setattr(
        flows, "stop_headed_worker",
        lambda pid: stopped.append(pid) or {"status": "stopped", "process_id": pid},
    )

    result = flows.stop_run(saved["id"], _request())

    assert result["run_id"] == queued["id"]
    assert result["status"] == "cancelled"
    assert stopped == [4321]
    assert flows.list_runs(flow_id=saved["id"], limit=100)[0]["status"] == "cancelled"


def test_asap_region_triplet_select_is_named_data_configuration():
    from app.flow_worker import _normalize_asap_filter_label

    options = [
        "MENA - Global - Global",
        "Global - Global - MENA",
        "Global - Global - CIS",
    ]
    assert _normalize_asap_filter_label(options[1], "select", options) == "Data Configuration"
    assert _normalize_asap_filter_label(options[1], "select", [options[1]]) == "Data Configuration"
    assert _normalize_asap_filter_label("Region", "select", options) == "Region"


def test_asap_duplicate_filter_discovery_merges_partial_and_complete_options():
    from app.flow_worker import _merge_asap_filter_definition

    definitions = []
    _merge_asap_filter_definition(
        definitions,
        "MENA - Global - Global",
        "select",
        ["MENA - Global - Global", "Global - Global - CIS"],
    )
    _merge_asap_filter_definition(
        definitions,
        "Global - Global - MENA",
        "select",
        [
            "MENA - Global - Global",
            "Global - Global - MENA",
            "Global - Global - CIS",
        ],
    )

    assert len(definitions) == 1
    assert definitions[0]["label"] == "Data Configuration"
    assert definitions[0]["options"] == [
        "MENA - Global - Global",
        "Global - Global - CIS",
        "Global - Global - MENA",
    ]


def test_asap_filter_discovery_reads_hidden_native_selects():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert 'frame.locator("select").all()' in source
    assert 'frame.locator("select:visible").all()' not in source
    assert 'control.locator("option").all_text_contents()' in source
    assert "wait_for_popup_options()" in source
    assert "time.monotonic() - stable_since >= 1.5" in source
    assert ".select2-results__option:visible" in source


def test_hidden_select2_control_is_selected_through_owning_native_select():
    from app.flow_worker import _select_native_options_by_text

    class Options:
        def __init__(self, labels):
            self.labels = labels

        def all_text_contents(self):
            return self.labels

    class Select:
        def __init__(self, labels, visible=False):
            self.labels = labels
            self.selected = []
            self.events = []

        def is_visible(self):
            return False

        def locator(self, selector):
            if selector == "option:checked":
                return Options([item for item in self.labels if item in self.selected])
            assert selector == "option"
            return Options(self.labels)

        def select_option(self, *, label, force):
            assert force is True
            self.selected = label
            self.events.extend(["input", "change"])
            return [label]

    class Selects:
        def __init__(self, controls):
            self.controls = controls

        def count(self):
            return len(self.controls)

        def nth(self, index):
            return self.controls[index]

    class Frame:
        page = SimpleNamespace(wait_for_timeout=lambda _ms: None)

        def __init__(self, controls):
            self.controls = controls

        def locator(self, selector):
            assert selector == "select"
            return Selects(self.controls)

    unrelated = Select(["MENA - Global - Global"])
    data_configuration = Select([
        "MENA - Global - Global",
        "Global - Global - MENA",
        "Global - Global - CIS",
    ])
    options = data_configuration.labels

    assert _select_native_options_by_text(
        Frame([unrelated, data_configuration]), [options[0]], options,
    ) is True
    assert unrelated.selected == []
    assert data_configuration.selected == options[0]
    assert data_configuration.events == ["input", "change"]


def _dimension_frame(states, *, sticky_on_replace=(), never_toggle=(), duplicate_canvas=()):
    events = []
    collection = []

    class Member:
        def __init__(self, text, state, x=20):
            self.text = text
            self.state = state
            self.x = x
            self.scroll_count = 0

        def is_visible(self):
            return True

        def bounding_box(self):
            return {"x": self.x, "y": 100 + collection.index(self) * 20}

        def scroll_into_view_if_needed(self, **_kwargs):
            self.scroll_count += 1

        def click(self, **_kwargs):
            events.append(("plain", self.text))
            for member in collection:
                if member.x < 160 and member.text not in sticky_on_replace:
                    member.state = False
            self.state = True

        def dispatch_event(self, name, event):
            assert name in {"mousedown", "mouseup", "click"}
            assert event["ctrlKey"] is True
            assert event["bubbles"] is True
            assert event["cancelable"] is True
            if name == "click":
                events.append(("local_ctrl", self.text))
            if name == "click" and self.text not in never_toggle:
                self.state = not self.state

        def evaluate(self, _script):
            return True if _script == "node => node.isConnected" else self.state

        def locator(self, selector):
            assert selector == "xpath=parent::*"
            return Parents([])

    class Members:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Parents(Members):
        @property
        def first(self):
            return self.items[0]

    class Root:
        def get_by_text(self, value, exact=False):
            return Members([member for member in collection if member.text == value and member.x < 160])

    for text, state in states.items():
        collection.append(Member(text, state))
    for text in duplicate_canvas:
        collection.append(Member(text, False, x=300))

    class Frame:
        page = SimpleNamespace(wait_for_timeout=lambda _ms: None)

        def get_by_text(self, value, exact=False):
            if hasattr(value, "pattern"):
                root = Root()
                heading = SimpleNamespace(
                    count=lambda: 1, is_visible=lambda: True,
                    locator=lambda _selector: Parents([root]),
                )
                return SimpleNamespace(first=heading)
            return Members([member for member in collection if member.text == value])

    return Frame(), collection, events


def test_dimension_replaces_stale_defaults_when_first_requested_is_already_selected():
    from app.flow_worker import _asap_select_dimensions

    frame, members, events = _dimension_frame(
        {"A": True, "B": True, "C": False}, duplicate_canvas=("A", "C"),
    )
    audit = _asap_select_dimensions(frame, ["A", "C"], ["A", "B", "C"])

    assert audit == {
        "filter": "Dimension",
        "requested": ["A", "C"],
        "initial_selected": ["A", "B"],
        "final_selected": ["A", "C"],
        "actual": ["A", "C"],
        "missing": [],
        "extra": [],
        "verified": True,
        "options": ["A", "B", "C"],
    }
    assert events == [("plain", "A"), ("local_ctrl", "C")]
    assert all(member.scroll_count for member in members if member.x < 160 and member.text in {"A", "C"})
    assert not any(member.scroll_count for member in members if member.x >= 160)


def test_dimension_performs_one_bounded_missing_and_extra_reconciliation_pass():
    from app.flow_worker import _asap_select_dimensions

    frame, members, events = _dimension_frame(
        {"A": False, "B": True, "C": False}, sticky_on_replace=("B",),
    )
    audit = _asap_select_dimensions(frame, ["A", "C"], ["A", "B", "C"])

    assert audit["final_selected"] == ["A", "C"]
    assert audit["missing"] == []
    assert audit["extra"] == []
    assert events == [
        ("plain", "A"), ("local_ctrl", "C"), ("local_ctrl", "B"),
    ]
    assert [member.text for member in members if member.x < 160 and member.state] == ["A", "C"]


def test_dimension_fails_before_run_when_reconciliation_cannot_reach_exact_set():
    from app.flow_worker import _asap_select_dimensions

    frame, _members, events = _dimension_frame(
        {"A": False, "B": False, "C": False}, never_toggle=("C",),
    )
    with pytest.raises(RuntimeError, match=r"Missing: \['C'\]"):
        _asap_select_dimensions(frame, ["A", "C"], ["A", "B", "C"])
    assert events == [
        ("plain", "A"), ("local_ctrl", "C"), ("local_ctrl", "C"),
    ]


def test_dimension_routine_never_sends_an_os_control_key():
    import inspect
    from app.flow_worker import _asap_local_dimension_click, _asap_select_dimensions

    source = inspect.getsource(_asap_select_dimensions) + inspect.getsource(_asap_local_dimension_click)
    assert 'keyboard.down("Control")' not in source
    assert 'modifiers=["Control"]' not in source
    assert '"ctrlKey": True' in source


def test_week_selection_replaces_stale_members_and_audits_exact_set():
    from app.flow_worker import _asap_select_week_values

    frame, members, events = _dimension_frame(
        {"202627": False, "202632": True, "202633": False},
    )
    audit = _asap_select_week_values(
        frame, "Sell-out Week", ["202627"], ["202627", "202632", "202633"],
    )

    assert audit["initial_selected"] == ["202632"]
    assert audit["final_selected"] == ["202627"]
    assert audit["missing"] == []
    assert audit["extra"] == []
    assert audit["verified"] is True
    assert events == [("plain", "202627")]
    assert [member.text for member in members if member.x < 160 and member.state] == ["202627"]


def test_week_selection_fails_before_run_when_exact_set_cannot_be_reached():
    from app.flow_worker import _asap_select_week_values

    frame, _members, events = _dimension_frame(
        {"202627": False, "202632": True},
        sticky_on_replace=("202632",), never_toggle=("202632",),
    )
    with pytest.raises(RuntimeError, match=r"Extra: \['202632'\]. The report was not run"):
        _asap_select_week_values(
            frame, "Sell-out Week", ["202627"], ["202627", "202632"],
        )
    assert events == [("plain", "202627"), ("local_ctrl", "202632")]


def test_asap_configuration_routes_dimension_and_week_through_audited_routines(monkeypatch):
    calls = []
    monkeypatch.setattr(
        flow_worker, "_asap_select_dimensions",
        lambda _frame, values, options: calls.append(("dimension", values, options)) or {
            "filter": "Dimension", "requested": values, "initial_selected": [],
            "final_selected": values, "actual": values, "missing": [], "extra": [],
            "verified": True, "options": options,
        },
    )
    monkeypatch.setattr(
        flow_worker, "_set_filter",
        lambda _frame, definition, value: calls.append(("ordinary", definition["control_label"], value)),
    )
    monkeypatch.setattr(
        flow_worker, "_asap_select_list_values",
        lambda _frame, label, values: calls.append(("list", label, values)),
    )
    monkeypatch.setattr(
        flow_worker, "_asap_select_week_values",
        lambda _frame, label, values, options: calls.append(("week", label, values, options)) or {
            "filter": label, "requested": values, "initial_selected": [],
            "final_selected": values, "actual": values, "missing": [], "extra": [],
            "verified": True, "options": options,
        },
    )
    monkeypatch.setattr(
        flow_worker, "_asap_read_dimension_selection",
        lambda _root, options: [value for value in options if value in {"A", "C", "202619"}],
    )
    monkeypatch.setattr(flow_worker, "_asap_dimension_root", lambda _frame, _requested: object())
    monkeypatch.setattr(flow_worker, "_asap_member_list_root", lambda _frame, _label, _requested: object())
    frame = SimpleNamespace(
        page=SimpleNamespace(wait_for_timeout=lambda _ms: None),
    )
    job = {
        "selections": {"dimension": ["A", "C"], "data_configuration": "Config A"},
        "report": {"filters": [
            {"filter_key": "dimension", "control_label": "Dimension", "control_type": "multi_select", "options": ["A", "B", "C"]},
            {"filter_key": "data_configuration", "control_label": "Data Configuration", "control_type": "select", "options": ["Config A"]},
            {"filter_key": "week", "control_label": "Sell-out Week", "control_type": "week", "options": ["202619"]},
        ]},
    }

    audit = flow_worker._asap_apply_configuration(frame, job, "2026-W19")

    assert calls == [
        ("dimension", ["A", "C"], ["A", "B", "C"]),
        ("ordinary", "Data Configuration", "Config A"),
        ("week", "Sell-out Week", ["202619"], ["202619"]),
    ]
    assert audit[0]["initial_selected"] == []
    assert audit[0]["final_selected"] == ["A", "C"]
    assert audit[0]["missing"] == []
    assert audit[0]["extra"] == []


def test_rendered_dimension_headers_must_match_exact_requested_set():
    class Candidate:
        def __init__(self, x, y):
            self.x = x
            self.y = y

        def is_visible(self):
            return True

        def bounding_box(self):
            return {"x": self.x, "y": self.y}

    class Candidates:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Frame:
        def get_by_text(self, value, exact=True):
            assert exact is True
            positions = {
                "A": [(20, 100), (300, 200)],
                "B": [(20, 120), (400, 204)],
                "C": [(20, 140), (500, 500)],
            }
            return Candidates([Candidate(x, y) for x, y in positions[value]])

    audit = [{
        "filter": "Dimension", "requested": ["A"],
        "options": ["A", "B", "C"],
    }]
    with pytest.raises(RuntimeError, match=r"Rendered: \['A', 'B'\].*Extra: \['B'\]"):
        flow_worker._asap_verify_rendered_results(Frame(), audit)


def test_targeted_refresh_stales_replaced_filter_definitions(flow_db):
    site, report = _seed_catalog()
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET discovery_key='Mobile > Report A', source_kind='discovered' WHERE id=?",
            (report["id"],),
        )
        db.execute(
            """UPDATE flow_report_filters SET filter_key='old_label', label='Old label',
               control_label='Old label', source_kind='discovered'
               WHERE report_id=? AND filter_key='region'""",
            (report["id"],),
        )
        flows._apply_discovery(
            db,
            site["id"],
            [flows.DiscoveredReport(
                discovery_key="Mobile > Report A",
                name="Report A",
                report_url="https://example.com/report-a",
                automation={"category_path": ["Mobile", "Report A"]},
                filters=[flows.DiscoveredFilter(
                    filter_key="new_label", label="New label", control_label="New label",
                    control_type="select", options=["A", "B"], position=0,
                )],
            )],
            "2026-08-12T10:00:00",
            complete=False,
        )
        rows = db.execute(
            """SELECT filter_key, enabled, stale FROM flow_report_filters
               WHERE report_id=? AND filter_key IN ('new_label', 'old_label') ORDER BY filter_key""",
            (report["id"],),
        ).fetchall()
    assert [(row["filter_key"], row["enabled"], row["stale"]) for row in rows] == [
        ("new_label", 1, 0), ("old_label", 0, 1),
    ]


def test_asap_week_detection_requires_page_discovered_iso_week_members():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert 're.fullmatch(r"20\\d{4}", value)' in source
    assert 'add_definition("Sell-out Week", "week", week_values)' in source


def test_flow_accepts_standard_iso_week_against_asap_option(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base (MENA)",
        name="Installed Base (MENA)",
        report_url="https://portal.example.test",
        filters=[flows.DiscoveredFilter(
            filter_key="sell_out_week", label="Sell-out Week", control_label="Sell-out Week",
            control_type="week", options=["202633"], position=0,
        )],
    )
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [report], "2026-08-12T10:00:00")
        report_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
        body = _flow(site["id"], report_id, selections={"sell_out_week": "2026-W33"})
        flows._validate_flow_selections(db, body)


def test_worker_api_retries_transient_server_errors(monkeypatch):
    attempts = []

    class Response:
        status_code = 500

        def raise_for_status(self):
            if len(attempts) < 3:
                request = __import__("httpx").Request("POST", "http://127.0.0.1/progress")
                raise __import__("httpx").HTTPStatusError(
                    "transient", request=request, response=self,
                )

        def json(self):
            return {"status": "running"}

    class Client:
        def request(self, *_args, **_kwargs):
            attempts.append(1)
            return Response()

    monkeypatch.setattr(flow_worker.time, "sleep", lambda _seconds: None)
    assert flow_worker._api(Client(), "POST", "/progress") == {"status": "running"}
    assert len(attempts) == 3


def test_asap_download_observes_every_open_portal_page():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert 'candidate.on("download", capture_download)' in source
    assert 'candidate.remove_listener("download", capture_download)' in source
    assert "download_page.expect_download" not in source
    assert "download, export_pages = _asap_download" in source
    assert "export_page.close(run_before_unload=False)" in source
    assert "candidate for candidate in wizard_pages" in source
    assert "page = _ensure_live_page(page)" in source
    assert '"stage": "next_period"' in source


def test_browser_failure_reporting_has_terminal_retry_and_fast_reaper():
    worker = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    main = Path(__file__).parents[1].joinpath("app", "main.py").read_text()
    assert "while not heartbeat_stop.wait(10)" in worker
    assert "for attempt in range(12)" in worker
    assert '"status": "failed"' in worker
    assert 'seconds=15' in main
    assert flows.fail_stale_runs.__defaults__ == (45,)


def test_stale_browser_run_is_failed_and_worker_released(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    old = "2026-08-13T10:00:00"
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:05:00"))
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET last_seen_at=? WHERE worker_id=?", (old, "bi-desktop-headed"))
        db.execute("UPDATE flow_runs SET heartbeat_at=? WHERE id=?", (old, queued["id"]))

    result = flows.fail_stale_runs(timeout_seconds=120)

    assert result == {"failed_run_ids": [queued["id"]], "count": 1}
    run = flows.get_run(queued["id"])
    assert run["status"] == "failed"
    assert "stopped responding" in run["error"]
    assert any(event["stage"] == "worker_lost" for event in run["events"])
    assert flows.list_flows()[0]["last_status"] == "failed"
    worker = next(item for item in flows.list_workers() if item["worker_id"] == "bi-desktop-headed")
    assert worker["current_run_id"] is None


def test_run_heartbeat_prevents_active_worker_from_being_reaped(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:05:00"))
    flows.heartbeat_run("bi-desktop-headed", queued["id"])

    assert flows.fail_stale_runs(timeout_seconds=120)["count"] == 0
    assert flows.get_run(queued["id"])["status"] == "claimed"
