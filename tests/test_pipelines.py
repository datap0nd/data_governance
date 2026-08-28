from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.database as database
import app.settings as settings
from app.database import get_db
from app.routers import pipelines
from app.source_identity import (
    exact_identity_rows,
    reconcile_flow_target,
    upsert_postgres_identity,
)
from app.scanner.tmdl_parser import _extract_table_navigation


@pytest.fixture
def pipeline_db(tmp_path, monkeypatch):
    path = str(tmp_path / "pipelines.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    database.init_db()
    return path


def _seed_pipeline(pipeline_db, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    server = "warehouse.example.test"
    database_name = "analytics"
    with get_db() as db:
        db.execute("UPDATE app_settings SET value='1' WHERE key='pipeline_full_refresh_enabled'")
        db.execute(
            "INSERT INTO people(id, name, role, email) VALUES (1, 'Report Owner', 'owner', 'owner@example.test')"
        )
        db.execute(
            "INSERT INTO reports(id, name, owner) VALUES (1, 'inflow outflow', ' Report Owner ')"
        )
        for source_id, name in (
            (10, "bi_reporting.inflow_outflow_mv"),
            (11, "bi_reporting.inflow"),
            (12, "bi_reporting.outflow"),
            (99, "bi_reporting.unrelated"),
        ):
            db.execute(
                "INSERT INTO sources(id, name, type, archived) VALUES (?, ?, 'postgresql', 0)",
                (source_id, name),
            )
        upsert_postgres_identity(
            db, source_id=10, server=server, database=database_name,
            schema="bi_reporting", relation="inflow_outflow_mv",
            relation_kind="materialized_view", verified_at=now,
        )
        for source_id, relation in ((11, "inflow"), (12, "outflow"), (99, "unrelated")):
            upsert_postgres_identity(
                db, source_id=source_id, server=server, database=database_name,
                schema="bi_reporting", relation=relation, verified_at=now,
            )
        db.execute("INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 10)")
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (10, 11)")
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (10, 12)")
        db.execute(
            "INSERT INTO flow_sites(id, name, adapter, base_url) VALUES (10, 'Portal', 'web_export', 'https://example.test')"
        )
        db.execute(
            "INSERT INTO flow_reports(id, site_id, name, report_url) VALUES (10, 10, 'Export', 'https://example.test/export')"
        )
        for flow_id, name, relation, source_id in (
            (20, "inflow", "inflow", 11),
            (21, "outflow", "outflow", 12),
            (22, "unrelated", "unrelated", 99),
        ):
            db.execute(
                """INSERT INTO flows
                       (id, name, site_id, report_id, target_folder, filename_template,
                        sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table,
                        sql_target_source_id, browser_mode)
                   VALUES (?, ?, 10, 10, 'C:\\Exports', '{flow}.csv', 1, 'append', ?,
                           'bi_reporting', ?, ?, 'headless')""",
                (flow_id, name, database_name, relation, source_id),
            )

    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", server)
    monkeypatch.setattr(
        pipelines,
        "configuration_status",
        lambda: {"configured": True, "missing": [], "host": server, "default_database": database_name},
    )
    monkeypatch.setattr(pipelines, "_probe_materialized_views", lambda _mvs: ([], []))
    monkeypatch.setattr(
        pipelines,
        "resolve_report_dataset",
        lambda _workspace, name: {
            "workspace": {"id": "11111111-1111-1111-1111-111111111111", "name": "Workspace"},
            "report_id": "22222222-2222-2222-2222-222222222222",
            "report_name": name,
            "dataset_id": "33333333-3333-3333-3333-333333333333",
            "web_url": "https://app.powerbi.test/report",
        },
    )


def _sql_job(table: str, *, database_name: str = "analytics", schema: str = "bi_reporting"):
    return pipelines._json({
        "sql_handoff": {
            "enabled": True,
            "database": database_name,
            "schema": schema,
            "table": table,
        }
    })


def test_exact_identity_keeps_quoted_case_and_database_collisions(pipeline_db):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute("INSERT INTO sources(id, name, type, archived) VALUES (1, 'Sales.Order', 'postgresql', 0)")
        db.execute("INSERT INTO sources(id, name, type, archived) VALUES (2, 'Sales.Order [other]', 'postgresql', 0)")
        upsert_postgres_identity(
            db, source_id=1, server="DB.EXAMPLE.TEST", database="Primary",
            schema="Sales", relation="Order", verified_at=now,
        )
        upsert_postgres_identity(
            db, source_id=2, server="db.example.test", database="Other",
            schema="Sales", relation="Order", verified_at=now,
        )
        primary = exact_identity_rows(
            db, server="db.example.test", database="Primary", schema="Sales", relation="Order"
        )
        wrong_case = exact_identity_rows(
            db, server="db.example.test", database="Primary", schema="sales", relation="order"
        )
    assert [row["source_id"] for row in primary] == [1]
    assert wrong_case == []


def test_tmdl_navigation_retains_quoted_identifier_spelling():
    relation = _extract_table_navigation(
        'Source{[Schema="Sales Data", Item="Order Details"]}[Data]'
    )
    assert relation == '"Sales Data"."Order Details"'


def test_flow_backfill_requires_one_exact_identity(pipeline_db, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute("INSERT INTO flow_sites(id, name) VALUES (100, 'Site')")
        db.execute("INSERT INTO flow_reports(id, site_id, name, report_url) VALUES (100, 100, 'Report', 'https://example.test')")
        db.execute("INSERT INTO sources(id, name, type, archived) VALUES (1, 'public.Target', 'postgresql', 0)")
        db.execute("INSERT INTO sources(id, name, type, archived) VALUES (2, 'public.Target duplicate', 'postgresql', 0)")
        for source_id in (1, 2):
            upsert_postgres_identity(
                db, source_id=source_id, server="db", database="analytics",
                schema="public", relation="Target", verified_at=now,
            )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table)
               VALUES (100, 'Writer', 100, 100, 'C:\\Exports', 'x.csv', 1, 'append',
                       'analytics', 'public', 'Target')"""
        )
        ambiguous = reconcile_flow_target(db, 100, server="db")
        db.execute("DELETE FROM source_postgres_identities WHERE source_id=2")
        confirmed = reconcile_flow_target(db, 100, server="db")
    assert ambiguous["status"] == "ambiguous"
    assert confirmed == {"status": "confirmed", "source_id": 1, "matches": [1]}


def test_inflow_outflow_plan_selects_both_flows_and_orders_mv(pipeline_db, monkeypatch):
    _seed_pipeline(pipeline_db, monkeypatch)
    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=True)
    assert plan["blockers"] == []
    assert [flow["name"] for flow in plan["flows"]] == ["inflow", "outflow"]
    assert [mv["relation"] for mv in plan["materialized_views"]] == ["inflow_outflow_mv"]
    assert plan["recipient"]["source"] == "report_owner"
    assert plan["powerbi"]["dataset_id"] == "33333333-3333-3333-3333-333333333333"


def test_ordinary_view_in_report_lineage_is_not_refreshed_as_mv(
    pipeline_db, monkeypatch
):
    _seed_pipeline(pipeline_db, monkeypatch)
    with get_db() as db:
        db.execute(
            "UPDATE source_postgres_identities SET relation_kind='view' WHERE source_id=10"
        )

    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert plan["blockers"] == []
    assert [flow["name"] for flow in plan["flows"]] == ["inflow", "outflow"]
    assert plan["materialized_views"] == []


def test_mv_order_collapses_paths_through_ordinary_views():
    ordered, cycle = pipelines._topological_mvs(
        {10, 30},
        [
            (10, 20),  # downstream MV -> ordinary view
            (20, 30),  # ordinary view -> upstream MV
            (30, 40),  # upstream MV -> base table
        ],
    )

    assert cycle == []
    assert ordered == [30, 10]


def test_plan_uses_unique_effective_flow_target_without_mutating(pipeline_db, monkeypatch):
    _seed_pipeline(pipeline_db, monkeypatch)
    original_timestamp = "2001-02-03T04:05:06+00:00"
    with get_db() as db:
        db.execute(
            "UPDATE flows SET sql_target_source_id=NULL, updated_at=? WHERE id=20",
            (original_timestamp,),
        )

    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    inflow = next(flow for flow in plan["flows"] if flow["id"] == 20)
    assert inflow["target_source_id"] == 11
    assert inflow["persisted_target_source_id"] is None
    assert inflow["target_resource_key"] == (
        "warehouse.example.test|analytics|bi_reporting|inflow"
    )
    with get_db() as db:
        row = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=20"
        ).fetchone()
    assert row["sql_target_source_id"] is None
    assert row["updated_at"] == original_timestamp


def test_plan_preserves_valid_stored_selection_when_exact_identity_is_duplicated(
    pipeline_db, monkeypatch
):
    _seed_pipeline(pipeline_db, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute(
            "INSERT INTO sources(id, name, type, archived) VALUES (13, 'duplicate inflow', 'postgresql', 0)"
        )
        upsert_postgres_identity(
            db, source_id=13, server="warehouse.example.test", database="analytics",
            schema="bi_reporting", relation="inflow", verified_at=now,
        )

    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert plan["blockers"] == []
    inflow = next(flow for flow in plan["flows"] if flow["id"] == 20)
    assert inflow["target_source_id"] == 11
    assert inflow["persisted_target_source_id"] == 11


def test_plan_detects_duplicate_physical_target_across_distinct_source_ids(
    pipeline_db, monkeypatch
):
    _seed_pipeline(pipeline_db, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute(
            "INSERT INTO sources(id, name, type, archived) VALUES (13, 'duplicate inflow', 'postgresql', 0)"
        )
        upsert_postgres_identity(
            db, source_id=13, server="warehouse.example.test", database="analytics",
            schema="bi_reporting", relation="inflow", verified_at=now,
        )
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (10, 13)")
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table,
                    sql_target_source_id, browser_mode)
               VALUES (23, 'inflow duplicate writer', 10, 10, 'C:\\Exports', 'x.csv',
                       1, 'append', 'analytics', 'bi_reporting', 'inflow', 13, 'headless')"""
        )

    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert any(
        "Multiple selected Flows write one output" in blocker
        for blocker in plan["blockers"]
    )


def test_plan_blocks_standalone_run_on_same_physical_target(pipeline_db, monkeypatch):
    _seed_pipeline(pipeline_db, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute(
            "INSERT INTO sources(id, name, type, archived) VALUES (13, 'duplicate inflow', 'postgresql', 0)"
        )
        upsert_postgres_identity(
            db, source_id=13, server="warehouse.example.test", database="analytics",
            schema="bi_reporting", relation="inflow", verified_at=now,
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table,
                    sql_target_source_id, browser_mode)
               VALUES (23, 'standalone inflow writer', 10, 10, 'C:\\Exports', 'x.csv',
                       1, 'append', 'analytics', 'bi_reporting', 'inflow', 13, 'headless')"""
        )
        cursor = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json)
               VALUES (23, 'manual', 'running', ?)""",
            (_sql_job("inflow"),),
        )
        active_run_id = int(cursor.lastrowid)

    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert any(
        f"standalone inflow writer' run #{active_run_id}" in blocker
        for blocker in plan["blockers"]
    )


def test_plan_uses_active_run_job_target_after_flow_configuration_changes(
    pipeline_db, monkeypatch
):
    _seed_pipeline(pipeline_db, monkeypatch)
    with get_db() as db:
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table,
                    browser_mode)
               VALUES (23, 'reconfigured writer', 10, 10, 'C:\\Exports', 'x.csv',
                       0, 'append', 'analytics', 'bi_reporting', 'outflow', 'headless')"""
        )
        cursor = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json)
               VALUES (23, 'manual', 'running', ?)""",
            (_sql_job("inflow"),),
        )
        active_run_id = int(cursor.lastrowid)

    plan = pipelines.build_refresh_plan(1, "Requester", probe_mvs=False)

    assert any(
        f"reconfigured writer' run #{active_run_id}" in blocker
        for blocker in plan["blockers"]
    )


def test_topological_sort_is_upstream_first_and_blocks_cycle():
    ordered, cycle = pipelines._topological_mvs({1, 2, 3}, [(1, 2), (2, 3)])
    assert ordered == [3, 2, 1]
    assert cycle == []
    _ordered, cycle = pipelines._topological_mvs({1, 2}, [(1, 2), (2, 1)])
    assert cycle


def test_preview_confirmation_persists_steps_and_reserves_resources(pipeline_db, monkeypatch):
    _seed_pipeline(pipeline_db, monkeypatch)
    with get_db() as db:
        db.execute(
            "UPDATE flows SET sql_target_source_id=NULL, updated_at='2001-01-01' WHERE id=20"
        )
        db.execute("UPDATE flows SET updated_at='2002-02-02' WHERE id=21")
    request = SimpleNamespace(state=SimpleNamespace(actor="Requester"))
    preview = pipelines.refresh_plan(1, request)
    with get_db() as db:
        assert db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=20"
        ).fetchone()[0] is None
    run = pipelines.create_pipeline_run(
        1, pipelines.RunCreate(plan_token=preview["plan_token"]), request
    )
    assert run["status"] == "queued"
    assert [step["step_type"] for step in run["steps"]] == [
        "flow", "flow", "mv", "powerbi", "notification"
    ]
    assert {lock["resource_type"] for lock in run["resource_locks"]} == {
        "report", "dataset", "flow", "flow_target", "mv"
    }
    inflow_step = next(
        step for step in run["steps"]
        if step["step_type"] == "flow" and step["entity_id"] == "20"
    )
    assert inflow_step["details"]["target_source_id"] == 11
    assert inflow_step["details"]["target"] == {
        "server": "warehouse.example.test",
        "database": "analytics",
        "schema": "bi_reporting",
        "table": "inflow",
    }
    with get_db() as db:
        flow = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=20"
        ).fetchone()
        assert flow["sql_target_source_id"] == 11
        assert flow["updated_at"] != "2001-01-01"
        assert db.execute(
            "SELECT updated_at FROM flows WHERE id=21"
        ).fetchone()[0] == "2002-02-02"
        with pytest.raises(HTTPException) as exc:
            pipelines.assert_resource_unlocked(db, "flow", "20")
    assert exc.value.status_code == 409


def test_run_confirmation_rolls_back_if_effective_target_cannot_be_persisted(
    pipeline_db, monkeypatch
):
    _seed_pipeline(pipeline_db, monkeypatch)
    request = SimpleNamespace(state=SimpleNamespace(actor="Requester"))
    preview = pipelines.refresh_plan(1, request)
    original_reconcile = pipelines.reconcile_flow_target

    def conflicting_reconcile(db, flow_id, *, server):
        if flow_id == 20:
            db.execute("UPDATE flows SET sql_target_source_id=12 WHERE id=20")
            return {"status": "target_changed", "source_id": 11, "matches": [11]}
        return original_reconcile(db, flow_id, server=server)

    monkeypatch.setattr(pipelines, "reconcile_flow_target", conflicting_reconcile)
    with pytest.raises(HTTPException) as exc:
        pipelines.create_pipeline_run(
            1, pipelines.RunCreate(plan_token=preview["plan_token"]), request
        )
    assert exc.value.status_code == 409
    with get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 0
        assert db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=20"
        ).fetchone()[0] == 11


def test_run_confirmation_rechecks_active_target_inside_write_transaction(
    pipeline_db, monkeypatch
):
    _seed_pipeline(pipeline_db, monkeypatch)
    request = SimpleNamespace(state=SimpleNamespace(actor="Requester"))
    preview = pipelines.refresh_plan(1, request)
    original_build = pipelines.build_refresh_plan
    inserted = []

    def build_then_queue_racing_flow(*args, **kwargs):
        plan = original_build(*args, **kwargs)
        if not inserted:
            with get_db() as db:
                db.execute(
                    """INSERT INTO flows
                           (id, name, site_id, report_id, target_folder, filename_template,
                            sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table,
                            browser_mode)
                       VALUES (23, 'racing writer', 10, 10, 'C:\\Exports', 'x.csv',
                               0, 'append', 'analytics', 'bi_reporting', 'outflow', 'headless')"""
                )
                cursor = db.execute(
                    """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json)
                       VALUES (23, 'manual', 'queued', ?)""",
                    (_sql_job("inflow"),),
                )
                inserted.append(int(cursor.lastrowid))
        return plan

    monkeypatch.setattr(pipelines, "build_refresh_plan", build_then_queue_racing_flow)
    with pytest.raises(HTTPException) as exc:
        pipelines.create_pipeline_run(
            1, pipelines.RunCreate(plan_token=preview["plan_token"]), request
        )

    assert exc.value.status_code == 409
    assert "racing writer" in str(exc.value.detail)
    with get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 0
        assert db.execute(
            "SELECT status FROM flow_runs WHERE id=?", (inserted[0],)
        ).fetchone()[0] == "queued"


def test_flow_resource_check_honors_physical_target_lock(pipeline_db, monkeypatch):
    _seed_pipeline(pipeline_db, monkeypatch)
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO pipeline_runs(report_id, status, stage, plan_hash, plan_json, dataset_id)
               VALUES (1, 'queued', 'queued', 'hash', '{}', 'dataset')"""
        )
        run_id = int(cursor.lastrowid)
        target_key = pipelines._flow_target_key_for_id(db, 20)
        db.execute(
            """INSERT INTO pipeline_resource_locks(resource_type, resource_key, run_id)
               VALUES ('flow_target', ?, ?)""",
            (target_key, run_id),
        )
        with pytest.raises(HTTPException) as exc:
            pipelines.assert_resource_unlocked(db, "flow", "20")
        pipelines.assert_resource_unlocked(db, "flow", "21")
    assert exc.value.status_code == 409
    assert "Flow output" in str(exc.value.detail)


def test_file_flow_is_visible_candidate_but_not_auto_executed_without_sql_credentials(
    pipeline_db, monkeypatch
):
    with get_db() as db:
        db.execute("UPDATE app_settings SET value='1' WHERE key='pipeline_full_refresh_enabled'")
        db.execute(
            "INSERT INTO people(id, name, role, email) VALUES (1, 'Owner', 'owner', 'owner@example.test')"
        )
        db.execute("INSERT INTO reports(id, name, owner) VALUES (1, 'Workbook report', 'Owner')")
        db.execute(
            """INSERT INTO sources(id, name, type, connection_info, archived)
               VALUES (30, 'daily.xlsx', 'excel', 'C:/Exports/daily.xlsx', 0)"""
        )
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 30)"
        )
        db.execute(
            "INSERT INTO flow_sites(id, name, adapter) VALUES (10, 'Portal', 'web_export')"
        )
        db.execute(
            """INSERT INTO flow_reports(id, site_id, name, report_url)
               VALUES (10, 10, 'Daily export', 'https://example.test/export')"""
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, browser_mode)
               VALUES (30, 'Daily workbook Flow', 10, 10, 'c:\\exports',
                       'DAILY.xlsx', 0, 'headless')"""
        )

    monkeypatch.setattr(
        pipelines,
        "configuration_status",
        lambda: {
            "configured": False,
            "missing": ["DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD"],
            "host": "",
            "default_database": "",
        },
    )
    monkeypatch.setattr(
        pipelines,
        "resolve_report_dataset",
        lambda _workspace, name: {
            "workspace": {"id": "workspace", "name": "Workspace"},
            "report_id": "report",
            "report_name": name,
            "dataset_id": "dataset",
            "web_url": "https://app.powerbi.test/report",
        },
    )
    request = SimpleNamespace(state=SimpleNamespace(actor="Owner"))

    preview = pipelines.refresh_plan(1, request)

    assert preview["blockers"] == []
    assert preview["flows"] == []
    diagnostic = next(
        item for item in preview["flow_diagnostics"]["items"] if item["id"] == 30
    )
    assert diagnostic["target_kind"] == "file"
    assert diagnostic["match_strategy"] == "exact_path"
    assert diagnostic["effective_source_id"] == 30
    assert diagnostic["scope_status"] == "candidate_in_report"
    assert diagnostic["reason_code"] == "file_output_candidate"
    assert diagnostic["executable"] is False
    assert pipelines.flow_target_resource_key_from_job({
        "downloads": {
            "target_folder": r"C:\Exports",
            "filename_template": "daily.xlsx",
        },
        "sql_handoff": {"enabled": False},
    }) == r"file|c:\exports\daily.xlsx"
    with get_db() as db, pytest.raises(HTTPException) as legacy_exc:
        pipelines._confirm_flow_targets_for_run(db, {
            "source_ids": [30],
            "flows": [{
                "id": 30,
                "name": "Daily workbook Flow",
                "target_kind": "file",
                "target_source_id": 30,
                "target_resource_key": r"file|c:\exports\daily.xlsx",
            }],
        })
    assert legacy_exc.value.status_code == 409
    assert "cannot be run automatically" in str(legacy_exc.value.detail)

    run = pipelines.create_pipeline_run(
        1,
        pipelines.RunCreate(plan_token=preview["plan_token"]),
        request,
    )

    assert run["status"] == "queued"
    assert not any(step["step_type"] == "flow" for step in run["steps"])
    with get_db() as db:
        locks = {
            (row["resource_type"], row["resource_key"])
            for row in db.execute(
                "SELECT resource_type, resource_key FROM pipeline_resource_locks WHERE run_id=?",
                (run["id"],),
            ).fetchall()
        }
        saved_link = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=30"
        ).fetchone()[0]
    assert ("flow_target", r"file|c:\exports\daily.xlsx") not in locks
    assert saved_link is None


def test_recipient_falls_back_only_to_unique_valid_requester(pipeline_db):
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name, owner) VALUES (1, 'Report', 'Missing Owner')")
        db.execute("INSERT INTO people(name, role, email) VALUES ('Requester', 'analyst', 'one@example.test')")
        report = db.execute("SELECT * FROM reports WHERE id=1").fetchone()
        recipient, error = pipelines._resolve_recipient(db, report, " requester ")
        db.execute("INSERT INTO people(name, role, email) VALUES ('REQUESTER', 'analyst', 'two@example.test')")
        blocked, blocked_error = pipelines._resolve_recipient(db, report, "requester")
    assert error == ""
    assert recipient["source"] == "requester"
    assert blocked is None
    assert "matched 2" in blocked_error


def test_mv_stage_commits_each_view_and_reports_partial_success(pipeline_db, monkeypatch):
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'MV test')")
        cursor = db.execute(
            """INSERT INTO pipeline_runs
                   (report_id, status, stage, plan_hash, plan_json, dataset_id)
               VALUES (1, 'refreshing_mvs', 'refreshing_mvs', 'hash', '{}', 'dataset')"""
        )
        run_id = int(cursor.lastrowid)
        for sequence, relation in enumerate(("upstream_mv", "downstream_mv")):
            db.execute(
                """INSERT INTO pipeline_run_steps
                       (run_id, step_type, sequence_no, entity_name, details_json)
                   VALUES (?, 'mv', ?, ?, ?)""",
                (
                    run_id, sequence, relation,
                    pipelines._json({"database": "analytics", "schema": "bi", "relation": relation}),
                ),
            )

    events = []

    class Result:
        def scalar_one(self):
            return 42

    class Connection:
        def execute(self, statement):
            sql = str(statement)
            events.append(sql)
            if "REFRESH MATERIALIZED VIEW" in sql and "downstream_mv" in sql:
                raise RuntimeError("lock timeout while another refresh is active")
            return Result()

    class Begin:
        def __enter__(self):
            events.append("BEGIN")
            return Connection()

        def __exit__(self, exc_type, exc, traceback):
            events.append("ROLLBACK" if exc else "COMMIT")
            return False

    class Engine:
        def begin(self):
            return Begin()

        def dispose(self):
            events.append("DISPOSE")

    monkeypatch.setattr(pipelines, "_engine", lambda _database: Engine())
    pipelines._run_mv_stage(run_id)

    with get_db() as db:
        run = db.execute("SELECT status, error FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        steps = db.execute(
            "SELECT status, row_count, row_count_status, details_json, error FROM pipeline_run_steps WHERE run_id=? ORDER BY sequence_no",
            (run_id,),
        ).fetchall()
    assert run["status"] == "failed"
    assert steps[0]["status"] == "succeeded"
    assert steps[0]["row_count"] == 42
    assert pipelines._loads(steps[0]["details_json"], {})["committed"] is True
    assert steps[1]["status"] == "failed"
    assert "lock timeout" in steps[1]["error"]
    assert events.count("COMMIT") == 2  # first refresh commit, then its separate COUNT(*)
    assert "ROLLBACK" in events


def test_restart_during_mv_marks_unknown_and_never_replays(pipeline_db, monkeypatch):
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Restart test')")
        cursor = db.execute(
            """INSERT INTO pipeline_runs
                   (report_id, status, stage, plan_hash, plan_json, dataset_id)
               VALUES (1, 'refreshing_mvs', 'refreshing_mvs', 'hash',
                       '{"materialized_views":[]}', 'dataset')"""
        )
        run_id = int(cursor.lastrowid)
        db.execute(
            """INSERT INTO pipeline_run_steps
                   (run_id, step_type, sequence_no, status, entity_name, operation_token)
               VALUES (?, 'mv', 0, 'running', 'possibly committed MV', 'old-process-token')""",
            (run_id,),
        )
        db.execute(
            "INSERT INTO pipeline_resource_locks(resource_type, resource_key, run_id) VALUES ('mv', 'key', ?)",
            (run_id,),
        )
    pipelines._futures.clear()
    monkeypatch.setattr("app.routers.email.reconcile_outlook_dispatches", lambda: {})

    pipelines.pipeline_tick()

    with get_db() as db:
        run = db.execute(
            "SELECT status, requires_inspection FROM pipeline_runs WHERE id=?", (run_id,)
        ).fetchone()
        step = db.execute(
            "SELECT status FROM pipeline_run_steps WHERE run_id=?", (run_id,)
        ).fetchone()
        lock = db.execute(
            "SELECT 1 FROM pipeline_resource_locks WHERE run_id=?", (run_id,)
        ).fetchone()
    assert (run["status"], run["requires_inspection"]) == ("failed", 1)
    assert step["status"] == "unknown"
    assert lock is None
