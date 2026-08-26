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


def test_topological_sort_is_upstream_first_and_blocks_cycle():
    ordered, cycle = pipelines._topological_mvs({1, 2, 3}, [(1, 2), (2, 3)])
    assert ordered == [3, 2, 1]
    assert cycle == []
    _ordered, cycle = pipelines._topological_mvs({1, 2}, [(1, 2), (2, 1)])
    assert cycle


def test_preview_confirmation_persists_steps_and_reserves_resources(pipeline_db, monkeypatch):
    _seed_pipeline(pipeline_db, monkeypatch)
    request = SimpleNamespace(state=SimpleNamespace(actor="Requester"))
    preview = pipelines.refresh_plan(1, request)
    run = pipelines.create_pipeline_run(
        1, pipelines.RunCreate(plan_token=preview["plan_token"]), request
    )
    assert run["status"] == "queued"
    assert [step["step_type"] for step in run["steps"]] == [
        "flow", "flow", "mv", "powerbi", "notification"
    ]
    assert {lock["resource_type"] for lock in run["resource_locks"]} == {
        "report", "dataset", "flow", "mv"
    }
    with get_db() as db:
        with pytest.raises(HTTPException) as exc:
            pipelines.assert_resource_unlocked(db, "flow", "20")
    assert exc.value.status_code == 409


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
