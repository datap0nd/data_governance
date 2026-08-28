import json
from datetime import datetime, timezone

import app.database as database
import app.settings as settings
from app.database import get_db
from app.flow_diagnostics import build_flow_diagnostics, diagnostic_blocker_messages
from app.routers import lineage, pipelines
from app.scanner.lifecycle import serialize_components
from app.source_identity import upsert_postgres_identity


def _insert_source(db, source_id, name, *, source_type="postgresql", archived=0):
    db.execute(
        """INSERT INTO sources(id, name, type, connection_info, archived)
           VALUES (?, ?, ?, ?, ?)""",
        (source_id, name, source_type, name, archived),
    )


def _insert_flow(
    db,
    flow_id,
    name,
    table,
    *,
    persisted_source_id=None,
    database_name="analytics",
    schema="sales",
    sql_handoff_enabled=1,
):
    db.execute(
        """INSERT INTO flows(
               id, name, site_id, report_id, target_folder, filename_template,
               sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table,
               sql_target_source_id, browser_mode)
           VALUES (?, ?, 100, 100, 'C:\\Exports', 'x.csv', ?, 'append', ?, ?, ?, ?, 'headless')""",
        (
            flow_id,
            name,
            sql_handoff_enabled,
            database_name,
            schema,
            table,
            persisted_source_id,
        ),
    )


def _seed_diagnostic_matrix(tmp_path, monkeypatch):
    path = str(tmp_path / "flow-diagnostics.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()
    server = "upload.example.test"
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute("UPDATE app_settings SET value='1' WHERE key='pipeline_full_refresh_enabled'")
        db.execute(
            "INSERT INTO people(id, name, role, email) VALUES (1, 'Owner', 'owner', 'owner@example.test')"
        )
        db.execute(
            """INSERT INTO reports(id, name, owner, pbi_dataset_id)
               VALUES (1, 'Flow Diagnostic Report', 'Owner',
                       '33333333-3333-3333-3333-333333333333')"""
        )
        db.execute(
            "INSERT INTO flow_sites(id, name, adapter) VALUES (100, 'Portal', 'web_export')"
        )
        db.execute(
            """INSERT INTO flow_reports(id, site_id, name, report_url)
               VALUES (100, 100, 'Export', 'https://example.test/export')"""
        )

        # Root plus every source that is evidence for this report's closure.
        _insert_source(db, 10, "report_root", source_type="excel")
        _insert_source(db, 11, "sales.connected")
        _insert_source(db, 12, "sales.ambiguous a")
        _insert_source(db, 13, "sales.ambiguous b")
        _insert_source(db, 14, "legacy.prefix.sales.legacy", source_type="excel")
        _insert_source(db, 15, "sales.stale", source_type="excel", archived=1)
        _insert_source(db, 16, "sales.old_target")
        _insert_source(db, 17, "sales.changed")
        _insert_source(db, 18, "sales.server_mismatch")
        _insert_source(db, 19, "sales.incomplete", source_type="excel")
        _insert_source(db, 20, "sales.outside")
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 10)"
        )
        for source_id in range(11, 20):
            db.execute(
                "INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (10, ?)",
                (source_id,),
            )

        for source_id, host, relation in (
            (11, server, "connected"),
            (12, server, "ambiguous"),
            (13, server, "ambiguous"),
            (16, server, "old_target"),
            (17, server, "changed"),
            (18, "catalog.example.test", "server_mismatch"),
            (20, server, "outside"),
        ):
            upsert_postgres_identity(
                db,
                source_id=source_id,
                server=host,
                database="analytics",
                schema="sales",
                relation=relation,
                verified_at=now,
            )

        _insert_flow(db, 101, "Connected", "connected")
        _insert_flow(db, 102, "Ambiguous", "ambiguous")
        _insert_flow(db, 103, "Outside", "outside", persisted_source_id=20)
        _insert_flow(db, 104, "Unknown", "missing")
        _insert_flow(db, 105, "Legacy", "legacy")
        _insert_flow(db, 106, "Stale", "stale", persisted_source_id=15)
        _insert_flow(db, 107, "Changed", "changed", persisted_source_id=16)
        _insert_flow(db, 108, "Server mismatch", "server_mismatch")
        _insert_flow(db, 109, "Incomplete", "", persisted_source_id=19)
        _insert_flow(db, 110, "Download only", None, sql_handoff_enabled=0)

        db.execute(
            """INSERT INTO scan_runs(status, components_json)
               VALUES ('completed_with_warnings', ?)""",
            (
                serialize_components(
                    {
                        "postgres_dependencies": {
                            "status": "completed_with_warnings",
                            "databases": {
                                "analytics": {"status": "completed"},
                                "staging": {"status": "failed", "error": "hidden"},
                            },
                        }
                    }
                ),
            ),
        )

    monkeypatch.setattr(lineage, "UPLOAD_PGHOST", server)
    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", server)
    monkeypatch.setattr(
        pipelines,
        "configuration_status",
        lambda: {
            "configured": True,
            "missing": [],
            "host": server,
            "default_database": "analytics",
        },
    )
    monkeypatch.setattr(pipelines, "_probe_materialized_views", lambda _mvs: ([], []))
    monkeypatch.setattr(
        pipelines,
        "resolve_report_dataset",
        lambda _workspace, name: {
            "workspace": {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Workspace",
            },
            "report_id": "22222222-2222-2222-2222-222222222222",
            "report_name": name,
            "dataset_id": "33333333-3333-3333-3333-333333333333",
            "web_url": "https://app.powerbi.test/report",
        },
    )
    return server


def test_flow_diagnostics_classify_exact_invalid_legacy_and_scope(tmp_path, monkeypatch):
    server = _seed_diagnostic_matrix(tmp_path, monkeypatch)
    with get_db() as db:
        diagnostics = build_flow_diagnostics(
            db,
            range(10, 20),
            server=server,
        )

    by_name = {item["name"]: item for item in diagnostics["items"]}
    assert diagnostics["included_count"] == 1
    assert diagnostics["excluded_count"] == 6
    assert diagnostics["download_only_count"] == 0

    connected = by_name["Connected"]
    assert connected["effective_source_id"] == 11
    assert connected["persisted_source_id"] is None
    assert connected["scope_status"] == "confirmed_in_report"
    assert connected["severity"] == "none"
    assert connected["executable"] is True
    assert connected["target"] == {
        "server": server,
        "database": "analytics",
        "schema": "sales",
        "table": "connected",
    }

    assert by_name["Ambiguous"]["reason_code"] == "ambiguous_target"
    assert by_name["Ambiguous"]["severity"] == "blocker"
    assert by_name["Ambiguous"]["scope_status"] == "candidate_in_report"
    assert by_name["Ambiguous"]["candidate_source_ids"] == [12, 13]
    # Diagnostics are report-scoped: unrelated and wholly undiscovered global
    # Flows do not appear as match/no-match noise for this report.
    assert "Outside" not in by_name
    assert "Unknown" not in by_name

    legacy = by_name["Legacy"]
    assert legacy["reason_code"] == "legacy_display_match"
    assert legacy["candidate_source_ids"] == [14]
    assert legacy["severity"] == "warning"
    assert legacy["executable"] is False

    stale = by_name["Stale"]
    assert stale["reason_code"] == "stale_target_link"
    assert stale["persisted_source_id"] == 15
    assert stale["scope_status"] == "candidate_in_report"
    assert stale["severity"] == "blocker"
    assert stale["executable"] is False

    changed = by_name["Changed"]
    assert changed["reason_code"] == "target_changed"
    assert changed["persisted_source_id"] == 16
    assert changed["effective_source_id"] == 17
    assert changed["candidate_source_ids"] == [17]
    assert changed["severity"] == "blocker"
    assert changed["executable"] is False

    mismatch = by_name["Server mismatch"]
    assert mismatch["reason_code"] == "server_mismatch"
    assert mismatch["candidate_source_ids"] == [18]
    assert mismatch["severity"] == "blocker"
    assert mismatch["executable"] is False

    assert by_name["Incomplete"]["reason_code"] == "incomplete_target"
    assert by_name["Incomplete"]["severity"] == "blocker"
    assert diagnostics["postgres_dependencies"] == {
        "status": "completed_with_warnings",
        "scan_run_id": 1,
        "databases": {
            "analytics": {"status": "completed"},
            "staging": {
                "status": "failed",
                "error": "Redacted; review server logs.",
            },
        },
    }


def test_lineage_and_refresh_plan_share_diagnostics_and_legacy_is_not_blocking(
    tmp_path, monkeypatch
):
    _seed_diagnostic_matrix(tmp_path, monkeypatch)

    diagram = lineage.get_lineage_diagram(1)
    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert diagram["flow_diagnostics"] == plan["flow_diagnostics"]
    assert [flow["name"] for flow in diagram["flows"]] == ["Connected"]
    assert [flow["name"] for flow in plan["flows"]] == ["Connected"]
    assert [item["name"] for item in diagram["legacy_flow_suggestions"]] == ["Legacy"]
    assert diagram["legacy_flow_suggestions"] == plan["legacy_flow_suggestions"]

    blockers = "\n".join(plan["blockers"])
    assert "Ambiguous" in blockers
    assert "Stale" in blockers
    assert "Changed" in blockers
    assert "Server mismatch" in blockers
    assert "Legacy" not in blockers
    assert "Outside" not in blockers
    assert "Unknown" not in blockers


def test_report_scoped_server_alias_gap_is_visible_but_never_auto_merged(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "server-alias-gap.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        db.execute(
            "UPDATE app_settings SET value='1' WHERE key='pipeline_full_refresh_enabled'"
        )
        db.execute(
            "INSERT INTO people(id, name, role, email) "
            "VALUES (1, 'Owner', 'owner', 'owner@example.test')"
        )
        db.execute(
            """INSERT INTO reports(id, name, owner, pbi_dataset_id)
               VALUES (1, 'Alias Gap Report', 'Owner',
                       '33333333-3333-3333-3333-333333333333')"""
        )
        _insert_source(db, 1, "inflow_outflow_mv")
        _insert_source(db, 2, "inflow_outflow_mv catalog")
        _insert_source(db, 3, "asap_import")
        for source_id, server, relation, kind in (
            (1, "powerbi-alias.internal", "inflow_outflow_mv", "table"),
            (2, "10.20.30.40", "inflow_outflow_mv", "materialized_view"),
            (3, "10.20.30.40", "asap_import", "table"),
        ):
            upsert_postgres_identity(
                db,
                source_id=source_id,
                server=server,
                database="warehouse",
                schema="reporting",
                relation=relation,
                relation_kind=kind,
                verified_at=now,
            )
        db.execute(
            "INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (2, 3)"
        )
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) "
            "VALUES (1, 'Model', 1)"
        )

        diagnostics = build_flow_diagnostics(
            db,
            [1],
            server="10.20.30.40",
            report_root_source_ids=[1],
        )

    assert len(diagnostics["items"]) == 1
    gap = diagnostics["items"][0]
    assert gap["diagnostic_kind"] == "lineage_gap"
    assert gap["reason_code"] == "server_alias_lineage_gap"
    assert gap["severity"] == "blocker"
    assert gap["persisted_source_id"] == 1
    assert gap["candidate_source_ids"] == [2]
    assert gap["target"] == {
        "server": "powerbi-alias.internal",
        "database": "warehouse",
        "schema": "reporting",
        "table": "inflow_outflow_mv",
    }
    assert "Power BI connection and PGHOST" in gap["message"]
    assert "did not merge them automatically" in gap["message"]
    assert diagnostic_blocker_messages(diagnostics) == [gap["message"]]

    # The synthetic diagnostic has no Flow ID. Both consumers must preserve it
    # as a visible blocker without trying to coerce its null ID to an integer.
    monkeypatch.setattr(lineage, "UPLOAD_PGHOST", "10.20.30.40")
    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", "10.20.30.40")
    monkeypatch.setattr(
        pipelines,
        "configuration_status",
        lambda: {
            "configured": True,
            "missing": [],
            "host": "10.20.30.40",
            "default_database": "warehouse",
        },
    )
    monkeypatch.setattr(
        pipelines,
        "resolve_report_dataset",
        lambda _workspace, name: {
            "workspace": {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Workspace",
            },
            "report_id": "22222222-2222-2222-2222-222222222222",
            "report_name": name,
            "dataset_id": "33333333-3333-3333-3333-333333333333",
            "web_url": "https://app.powerbi.test/report",
        },
    )

    diagram = lineage.get_lineage_diagram(1)
    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert diagram["flow_diagnostics"]["items"] == [gap]
    assert plan["flow_diagnostics"]["items"] == [gap]
    assert diagram["flows"] == []
    assert plan["flows"] == []
    assert gap["message"] in plan["blockers"]


def test_server_alias_gap_ignores_upstream_leaves_archived_catalog_and_other_hosts(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "server-alias-gap-scope.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        for source_id, name, archived in (
            (1, "report_mv", 0),
            (2, "shared_leaf", 0),
            (3, "unrelated_shared_leaf", 0),
            (4, "unrelated_dependency", 0),
            (5, "archived_candidate_root", 0),
            (6, "archived_catalog_object", 1),
            (7, "catalog_dependency", 0),
            (8, "other_host_root", 0),
            (9, "other_host_catalog_object", 0),
        ):
            _insert_source(db, source_id, name, archived=archived)

        for source_id, host, relation, kind in (
            (1, "10.20.30.40", "report_mv", "materialized_view"),
            (2, "10.20.30.40", "shared_leaf", "table"),
            (3, "dev.example.test", "shared_leaf", "view"),
            (4, "dev.example.test", "leaf_input", "table"),
            (5, "powerbi-alias.internal", "archived_root", "table"),
            (6, "10.20.30.40", "archived_root", "materialized_view"),
            (7, "10.20.30.40", "catalog_input", "table"),
            (8, "powerbi-alias.internal", "wrong_host_root", "table"),
            (9, "dev.example.test", "wrong_host_root", "materialized_view"),
        ):
            upsert_postgres_identity(
                db,
                source_id=source_id,
                server=host,
                database="warehouse",
                schema="reporting",
                relation=relation,
                relation_kind=kind,
                verified_at=now,
            )

        for source_id, depends_on_id in ((1, 2), (3, 4), (6, 7), (9, 4)):
            db.execute(
                """INSERT INTO source_dependencies(source_id, depends_on_id)
                   VALUES (?, ?)""",
                (source_id, depends_on_id),
            )

        diagnostics = build_flow_diagnostics(
            db,
            [1, 2, 5, 8],
            server="10.20.30.40",
            report_root_source_ids=[1, 5, 8],
        )

    assert diagnostics["items"] == []


def test_diagnostics_are_safe_for_empty_and_legacy_scan_databases(tmp_path, monkeypatch):
    path = str(tmp_path / "empty-diagnostics.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()

    with get_db() as db:
        empty = build_flow_diagnostics(db, [], server="db")
        db.execute("INSERT INTO scan_runs(status, components_json) VALUES ('completed', NULL)")
        legacy = build_flow_diagnostics(db, [], server="db")

    assert empty == {
        "included_count": 0,
        "excluded_count": 0,
        "download_only_count": 0,
        "items": [],
        "postgres_dependencies": {
            "status": "not_scanned",
            "scan_run_id": None,
            "databases": {},
        },
    }
    assert legacy["postgres_dependencies"] == {
        "status": "unknown",
        "scan_run_id": 1,
        "databases": {},
    }


def test_focused_lineage_job_becomes_latest_diagnostic_evidence(tmp_path, monkeypatch):
    path = str(tmp_path / "focused-lineage-diagnostics.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()

    with get_db() as db:
        db.execute(
            """INSERT INTO scan_runs(started_at, finished_at, status, components_json)
               VALUES ('2026-08-28T08:00:00+00:00', '2026-08-28T08:05:00+00:00',
                       'failed', ?)""",
            (serialize_components({
                "postgres_dependencies": {
                    "status": "failed",
                    "databases": {"warehouse": {"status": "failed"}},
                }
            }),),
        )
        db.execute(
            """INSERT INTO scanner_jobs
                   (job_type, trigger_source, status, current_step, result_json,
                    context_json, created_at, heartbeat_at, finished_at)
               VALUES ('postgres_lineage', 'pipeline_recheck', 'completed',
                       'Finished', ?, ?, '2026-08-28T09:00:00+00:00',
                       '2026-08-28T09:03:00+00:00', '2026-08-28T09:03:00+00:00')""",
            (
                json.dumps({
                    "status": "completed",
                    "databases": {"warehouse": {"status": "completed"}},
                    "report_identity_reconciliation": {
                        "status": "completed",
                        "report_id": 7,
                        "relinked": 1,
                    },
                }),
                json.dumps({"report_id": 7}),
            ),
        )
        diagnostics = build_flow_diagnostics(db, [], server="db", report_id=7)

    assert diagnostics["postgres_dependencies"] == {
        "status": "completed",
        "scan_run_id": None,
        "scanner_job_id": 1,
        "databases": {"warehouse": {"status": "completed"}},
        "report_identity_reconciliation": {
            "status": "completed",
            "report_id": 7,
            "relinked": 1,
        },
    }


def test_focused_lineage_diagnostics_keep_top_level_endpoint_warnings(
    tmp_path,
    monkeypatch,
):
    path = str(tmp_path / "focused-lineage-endpoints.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()
    result = {
        "status": "completed_with_warnings",
        "databases": {"warehouse": {"status": "completed"}},
        "report_identity_reconciliation": {
            "status": "completed",
            "report_id": 7,
        },
        "unconfigured_catalog_targets": [{
            "server": "other.internal:5433",
            "database": "legacy",
            "reason_code": "unconfigured_catalog_endpoint",
        }],
        "unattempted_catalog_targets": [{
            "server": "db.internal",
            "database": "new_db",
            "reason_code": "catalog_target_became_active_during_scan",
        }],
    }
    with get_db() as db:
        db.execute(
            """INSERT INTO scanner_jobs
                   (job_type, trigger_source, status, current_step, result_json,
                    context_json, created_at, heartbeat_at, finished_at)
               VALUES ('postgres_lineage', 'pipeline_recheck',
                       'completed_with_warnings', 'Finished', ?, ?,
                       '2026-08-28T09:00:00+00:00', '2026-08-28T09:01:00+00:00',
                       '2026-08-28T09:01:00+00:00')""",
            (json.dumps(result), json.dumps({"report_id": 7})),
        )
        diagnostics = build_flow_diagnostics(db, [], server="db", report_id=7)

    postgres = diagnostics["postgres_dependencies"]
    assert postgres["unconfigured_catalog_targets"] == result[
        "unconfigured_catalog_targets"
    ]
    assert postgres["unattempted_catalog_targets"] == result[
        "unattempted_catalog_targets"
    ]


def test_focused_lineage_diagnostics_never_borrow_another_reports_job(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "report-scoped-lineage-diagnostics.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()

    def focused_result(report_id: int, status: str) -> str:
        return json.dumps({
            "status": status,
            "databases": {
                f"warehouse_{report_id}": {"status": status},
            },
            "report_identity_reconciliation": {
                "status": status,
                "report_id": report_id,
            },
        })

    with get_db() as db:
        db.execute(
            """INSERT INTO scanner_jobs
                   (job_type, trigger_source, status, current_step, result_json,
                    context_json, created_at, heartbeat_at, finished_at)
               VALUES ('postgres_lineage', 'pipeline_recheck', 'completed',
                       'Finished A', ?, ?, '2026-08-28T09:00:00+00:00',
                       '2026-08-28T09:01:00+00:00', '2026-08-28T09:01:00+00:00')""",
            (focused_result(7, "completed"), json.dumps({"report_id": 7})),
        )
        db.execute(
            """INSERT INTO scanner_jobs
                   (job_type, trigger_source, status, current_step, result_json,
                    context_json, created_at, heartbeat_at, finished_at)
               VALUES ('postgres_lineage', 'pipeline_recheck',
                       'completed_with_warnings', 'Finished B', ?, ?,
                       '2026-08-28T10:00:00+00:00', '2026-08-28T10:01:00+00:00',
                       '2026-08-28T10:01:00+00:00')""",
            (
                focused_result(8, "completed_with_warnings"),
                json.dumps({"report_id": 8}),
            ),
        )

        report_a = build_flow_diagnostics(db, [], server="db", report_id=7)
        report_b = build_flow_diagnostics(db, [], server="db", report_id=8)
        unscoped = build_flow_diagnostics(db, [], server="db")

    assert report_a["postgres_dependencies"]["scanner_job_id"] == 1
    assert report_a["postgres_dependencies"]["status"] == "completed"
    assert report_a["postgres_dependencies"]["report_identity_reconciliation"][
        "report_id"
    ] == 7
    assert report_b["postgres_dependencies"]["scanner_job_id"] == 2
    assert report_b["postgres_dependencies"]["status"] == "completed_with_warnings"
    assert report_b["postgres_dependencies"]["report_identity_reconciliation"][
        "report_id"
    ] == 8
    assert unscoped["postgres_dependencies"] == {
        "status": "not_scanned",
        "scan_run_id": None,
        "databases": {},
    }


def test_global_full_scan_repair_warning_is_not_attributed_to_selected_report(
    tmp_path,
    monkeypatch,
):
    path = str(tmp_path / "global-lineage-warning.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()
    component = {
        "status": "completed_with_warnings",
        "databases": {"alpha": {"status": "completed"}},
        "report_identity_reconciliation": {
            "status": "completed_with_warnings",
            "report_id": None,
            "issues": [{
                "reason_code": "unconfigured_catalog_endpoint",
                "server": "other.internal",
                "database": "beta",
            }],
        },
    }
    with get_db() as db:
        db.execute(
            """INSERT INTO scan_runs(status, finished_at, components_json)
               VALUES ('completed_with_warnings', '2026-08-28T11:00:00+00:00', ?)""",
            (serialize_components({"postgres_dependencies": component}),),
        )
        selected = build_flow_diagnostics(db, [], server="db", report_id=7)
        unscoped = build_flow_diagnostics(db, [], server="db")

    assert "report_identity_reconciliation" not in selected["postgres_dependencies"]
    assert unscoped["postgres_dependencies"]["report_identity_reconciliation"] == (
        component["report_identity_reconciliation"]
    )


def test_file_flow_uses_exact_path_then_unique_static_basename(tmp_path, monkeypatch):
    server = _seed_diagnostic_matrix(tmp_path, monkeypatch)
    with get_db() as db:
        _insert_source(db, 30, "daily.xlsx", source_type="excel")
        db.execute(
            "UPDATE sources SET connection_info=? WHERE id=30",
            (r"D:\\PowerBI\\#77_28-08-2026\\daily.xlsx",),
        )
        db.execute(
            "INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (10, 30)"
        )
        _insert_flow(
            db,
            120,
            "Daily workbook",
            None,
            sql_handoff_enabled=0,
        )
        db.execute(
            "UPDATE flows SET target_folder=?, filename_template=? WHERE id=120",
            (r"C:\\Exports", "daily.xlsx"),
        )

        diagnostics = build_flow_diagnostics(
            db,
            [*range(10, 20), 30],
            server=server,
        )

    daily = next(item for item in diagnostics["items"] if item["id"] == 120)
    assert daily["target_kind"] == "file"
    assert daily["effective_source_id"] == 30
    assert daily["match_strategy"] == "unique_basename"
    assert daily["link_status"] == "candidate"
    assert daily["scope_status"] == "candidate_in_report"
    assert daily["reason_code"] == "file_output_candidate"
    assert daily["severity"] == "warning"
    assert daily["executable"] is False


def test_file_flow_does_not_authorize_ambiguous_or_dynamic_filename(tmp_path, monkeypatch):
    server = _seed_diagnostic_matrix(tmp_path, monkeypatch)
    with get_db() as db:
        _insert_source(db, 30, "daily.xlsx", source_type="excel")
        db.execute(
            "UPDATE sources SET connection_info=? WHERE id=30",
            (r"D:\\PowerBI\\daily.xlsx",),
        )
        _insert_source(db, 31, "daily duplicate", source_type="excel")
        db.execute(
            "UPDATE sources SET connection_info=? WHERE id=31",
            (r"E:\\Other\\daily.xlsx",),
        )
        db.execute(
            "INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (10, 30)"
        )
        for flow_id, name, filename in (
            (120, "Ambiguous workbook", "daily.xlsx"),
            (121, "Dynamic workbook", "daily_{week}.xlsx"),
            (122, "Unrelated workbook", "other.xlsx"),
        ):
            _insert_flow(db, flow_id, name, None, sql_handoff_enabled=0)
            db.execute(
                "UPDATE flows SET target_folder=?, filename_template=? WHERE id=?",
                (r"C:\\Exports", filename, flow_id),
            )

        diagnostics = build_flow_diagnostics(
            db,
            [*range(10, 20), 30],
            server=server,
        )

    by_name = {item["name"]: item for item in diagnostics["items"]}
    assert by_name["Ambiguous workbook"]["reason_code"] == "ambiguous_file_target"
    assert by_name["Ambiguous workbook"]["severity"] == "warning"
    assert by_name["Ambiguous workbook"]["executable"] is False
    assert "Dynamic workbook" not in by_name
    assert "Unrelated workbook" not in by_name
