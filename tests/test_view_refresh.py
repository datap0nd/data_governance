"""Post-SQL materialized-view refresh: setup, execution, persistence and recovery."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database, flow_activity, flow_standalone as standalone, flow_worker
from app import flow_view_refresh as executor
from app import flow_view_refresh_discovery as discovery
from app.routers import flows, pipelines
from app.source_identity import upsert_postgres_identity
from test_flows import flow_db, _flow, _request, _seed_catalog, _mark_discovered
from test_flow_standalone import local_job

SERVER = "warehouse.example.test"
NOW = datetime.now(timezone.utc).isoformat()


class FakeEngines:
    """Records REFRESH statements per database; optional failures per view."""

    def __init__(self, fail=(), kinds=None, owners=None):
        self.events = []
        self.fail = set(fail)
        self.kinds = kinds or {}
        self.owners = owners or {}
        self.opened = []

    def __call__(self, database):
        engines = self

        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

            def fetchall(self):
                return []

        class Connection:
            def execute(self, statement, params=None):
                sql = str(statement)
                engines.events.append((database, sql))
                if "REFRESH MATERIALIZED VIEW" in sql:
                    if any(f'"{name}"' in sql for name in engines.fail):
                        raise RuntimeError("canceling statement due to lock timeout")
                    return Result(None)
                if "pg_get_userbyid" in sql:
                    key = f"{params['schema']}.{params['name']}"
                    if key not in engines.kinds:
                        return Result(None)
                    return Result((engines.kinds[key], engines.owners.get(key, "flow_writer"), engines.owners.get(key, "flow_writer") == "flow_writer"))
                return Result(None)

            def __enter__(self):
                return self

            def __exit__(self, kind, exc, tb):
                engines.events.append((database, "ROLLBACK" if exc else "COMMIT"))
                return False

        class Engine:
            def begin(self):
                engines.events.append((database, "BEGIN"))
                return Connection()

            def connect(self):
                return Connection()

            def dispose(self):
                engines.events.append((database, "DISPOSE"))

        self.opened.append(database)
        return Engine()


def view(schema, name, database="analytics"):
    return {"database": database, "schema": schema, "name": name}


# ---------------------------------------------------------------- executor --

def test_config_normalization_defaults_to_off_and_validates_manual_views():
    assert executor.normalize_config(None) == {"mode": "off", "views": []}
    assert executor.normalize_config({"mode": "automatic", "views": [view("a", "b")]}) == {"mode": "automatic", "views": []}
    manual = executor.normalize_config({"mode": "manual", "views": [view("bi", "Sales MV"), view("bi", "Sales MV"), {"database": "analytics", "schema": "bi", "view": "orders_mv"}]})
    assert [v["name"] for v in manual["views"]] == ["Sales MV", "orders_mv"]
    with pytest.raises(ValueError, match="at least one"):
        executor.normalize_config({"mode": "manual", "views": []})
    with pytest.raises(ValueError, match="Off, Automatic or Manual"):
        executor.normalize_config({"mode": "sometimes"})
    with pytest.raises(ValueError, match="database, schema and view name"):
        executor.normalize_config({"mode": "manual", "views": [{"schema": "bi"}]})


def test_order_views_is_upstream_first_through_ordinary_views_and_detects_cycles():
    views = [view("bi", "top_mv"), view("bi", "base_mv"), view("bi", "middle_mv")]
    edges = {"analytics": [(("bi", "top_mv"), ("bi", "helper_view")), (("bi", "helper_view"), ("bi", "middle_mv")),
                           (("bi", "middle_mv"), ("bi", "base_mv")), (("bi", "base_mv"), ("public", "sales"))]}
    ordered, cycle = executor.order_views(views, edges)
    assert [v["name"] for v in ordered] == ["base_mv", "middle_mv", "top_mv"] and cycle == []
    edges["analytics"].append((("bi", "base_mv"), ("bi", "top_mv")))
    _ordered, cycle = executor.order_views(views, edges)
    assert cycle and cycle[0] == cycle[-1]
    # Views without edge information keep their configured order.
    assert [v["name"] for v in executor.order_views([view("x", "b"), view("x", "a")], {})[0]] == ["b", "a"]


def test_refresh_commits_each_view_separately_and_stops_after_a_failure(tmp_path):
    engines = FakeEngines(fail={"middle_mv"})
    events = []
    checkpoint = tmp_path / "checkpoint.json"
    views = [view("bi", "base_mv"), view("bi", "middle_mv"), view("bi", "top_mv")]
    with pytest.raises(executor.ViewRefreshError) as failure:
        executor.refresh_views(views, events.append, engine_factory=engines, checkpoint=checkpoint)
    statuses = [(item["name"], item["status"]) for item in failure.value.results]
    assert statuses == [("base_mv", "succeeded"), ("middle_mv", "failed"), ("top_mv", "skipped")]
    assert failure.value.results[1]["error"] and failure.value.results[1]["duration_ms"] is not None
    assert "SQL insertion had already committed" in str(failure.value)
    sql = [event for event in engines.events if event[1] not in {"DISPOSE"}]
    assert [s for _, s in sql if s in {"BEGIN", "COMMIT", "ROLLBACK"}] == ["BEGIN", "COMMIT", "BEGIN", "ROLLBACK"]
    assert 'REFRESH MATERIALIZED VIEW "bi"."base_mv"' in [s for _, s in sql]
    assert [event["stage"] for event in events] == ["view_refresh", "view_refresh", "view_refresh_failed"]
    assert events[-1]["view_refresh"]["completed"] == 1 and events[-1]["view_refresh"]["current"] == "analytics|bi|middle_mv"
    assert executor.read_checkpoint(checkpoint) == ["analytics|bi|base_mv"]
    # A retry skips the checkpointed view and finishes the chain; the checkpoint is then removed.
    engines2 = FakeEngines()
    results = executor.refresh_views(views, events.append, engine_factory=engines2, completed=executor.read_checkpoint(checkpoint), checkpoint=checkpoint)
    assert [item["status"] for item in results] == ["succeeded"] * 3 and results[0].get("skipped") == "completed_earlier"
    refreshed = [s for _, s in engines2.events if "REFRESH" in s]
    assert len(refreshed) == 2 and all("base_mv" not in s for s in refreshed)
    assert events[-1]["stage"] == "view_refresh_complete" and not checkpoint.exists()


def test_quoted_identifiers_and_duplicates_are_handled_exactly():
    assert executor.qualified(view('Mixed Case', 'Sales "MV"')) == '"Mixed Case"."Sales ""MV"""'
    config = executor.normalize_config({"mode": "manual", "views": [view("bi", "mv"), view("bi", "MV"), view("bi", "mv")]})
    assert [v["name"] for v in config["views"]] == ["mv", "MV"]


def test_verification_reports_missing_wrong_kind_and_permission():
    engines = FakeEngines(kinds={"bi.sales_mv": "m", "bi.plain_view": "v", "bi.locked_mv": "m"}, owners={"bi.locked_mv": "dba"})
    findings = executor.inspect_views([view("bi", "sales_mv"), view("bi", "plain_view"), view("bi", "locked_mv"), view("bi", "missing")], engine_factory=engines)
    problems = executor.verification_problems(findings)
    assert findings[0]["can_refresh"] is True
    assert any("is a view, not a materialized view" in p for p in problems)
    assert any("cannot refresh analytics.bi.locked_mv" in p for p in problems)
    assert any("does not exist" in p for p in problems)
    with pytest.raises(RuntimeError, match="before SQL insertion"):
        executor.verify_views([view("bi", "missing")], engine_factory=engines)
    assert not any("REFRESH" in s for _, s in engines.events)


# --------------------------------------------------------------- discovery --

def _seed_metadata(db, *, verified=NOW, extra=None):
    """table -> ordinary view -> mv_a -> mv_b, plus an unrelated mv."""
    rows = [(1, "public.flow_table", "public", "flow_table", "table"), (2, "bi.helper_view", "bi", "helper_view", "view"),
            (3, "bi.mv_a", "bi", "mv_a", "materialized_view"), (4, "bi.mv_b", "bi", "mv_b", "materialized_view"),
            (5, "bi.unrelated_mv", "bi", "unrelated_mv", "materialized_view")]
    for source_id, name, schema, relation, kind in rows + (extra or []):
        db.execute("INSERT OR IGNORE INTO sources(id, name, type, archived) VALUES (?, ?, 'postgresql', 0)", (source_id, name))
        upsert_postgres_identity(db, source_id=source_id, server=SERVER, database="analytics", schema=schema,
                                 relation=relation, relation_kind=kind, verified_at=verified)
    for dependent, upstream in ((2, 1), (3, 2), (4, 3)):
        db.execute("INSERT OR IGNORE INTO source_dependencies(source_id, depends_on_id) VALUES (?, ?)", (dependent, upstream))


def test_automatic_discovery_walks_the_full_downstream_chain_upstream_first(flow_db):
    with database.get_db() as db:
        _seed_metadata(db)
        result = discovery.discover_automatic(db, SERVER, {"database": "analytics", "schema": "public", "table": "flow_table"})
        assert result["status"] == "ok" and result["stale"] is False
        assert [v["name"] for v in result["views"]] == ["mv_a", "mv_b"]
        assert result["metadata_at"] == NOW
        # Exact identity: a different schema is a different table.
        missing = discovery.discover_automatic(db, SERVER, {"database": "analytics", "schema": "staging", "table": "flow_table"})
        assert missing["status"] == "missing" and "Refresh the metadata" in missing["blockers"][0]
        # Verified empty list: the unrelated view has nothing downstream.
        empty = discovery.discover_automatic(db, SERVER, {"database": "analytics", "schema": "bi", "table": "unrelated_mv"})
        assert empty["status"] == "ok" and empty["views"] == []
        assert discovery.discover_automatic(db, SERVER, {"database": "", "schema": "", "table": ""})["status"] == "unconfigured"


def test_automatic_discovery_distinguishes_incomplete_stale_and_cyclic_lineage(flow_db):
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with database.get_db() as db:
        _seed_metadata(db, verified=old)
        db.execute("INSERT INTO sources(id, name, type, archived) VALUES (9, 'bi.no_identity', 'postgresql', 0)")
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (9, 3)")
        result = discovery.discover_automatic(db, SERVER, {"database": "analytics", "schema": "public", "table": "flow_table"})
        assert result["status"] == "incomplete" and "no_identity" in result["blockers"][0]
        db.execute("DELETE FROM source_dependencies WHERE source_id=9")
        stale = discovery.discover_automatic(db, SERVER, {"database": "analytics", "schema": "public", "table": "flow_table"})
        assert stale["status"] == "ok" and stale["stale"] is True and "older than 48 hours" in stale["warnings"][0]
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (2, 4)")
        cyclic = discovery.discover_automatic(db, SERVER, {"database": "analytics", "schema": "public", "table": "flow_table"})
        assert cyclic["status"] == "cyclic" and "cycle" in cyclic["blockers"][0]


def test_manual_verification_orders_by_catalog_and_postgres_dependencies(flow_db):
    engines = FakeEngines(kinds={"bi.mv_a": "m", "bi.mv_b": "m", "bi.extra_mv": "m"})
    with database.get_db() as db:
        _seed_metadata(db)
        result = discovery.verify_manual(db, SERVER, [view("bi", "mv_b"), view("bi", "mv_a"), view("bi", "extra_mv")], engine_factory=engines)
    assert result["status"] == "ok"
    assert [v["name"] for v in result["views"]] == ["mv_a", "mv_b", "extra_mv"]
    assert result["views"][2]["catalog"] is False and all(v["verified"] for v in result["views"])
    assert not any("REFRESH" in s for _, s in engines.events)
    with pytest.raises(ValueError):
        with database.get_db() as db:
            discovery.verify_manual(db, SERVER, [{"schema": "bi"}], engine_factory=engines)


# -------------------------------------------------------- flow configuration --

def _sql_flow(**overrides):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute("""INSERT INTO flow_sql_catalog(database_name, schema_name, table_name, last_seen_at, stale)
                      VALUES ('analytics', 'public', 'flow_table', ?, 0)""", (NOW,))
    body = _flow(site["id"], report["id"], enabled=False, schedule_type="manual", sql_handoff_enabled=True, sql_mode="append",
                 sql_database="analytics", sql_schema="public", sql_table="flow_table", **overrides)
    return flows.create_flow(body, _request())


def test_flow_setting_defaults_off_persists_and_survives_older_clients(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    saved = _sql_flow()
    assert saved["post_sql_refresh"] == {"mode": "off", "views": []}
    manual = _flow(saved["site_id"], saved["report_id"], enabled=False, schedule_type="manual",
        sql_handoff_enabled=True, sql_mode="append", sql_database="analytics", sql_schema="public", sql_table="flow_table",
        post_sql_refresh={"mode": "manual", "views": [view("bi", "mv_b"), view("bi", "mv_a")]})
    updated = flows.update_flow(saved["id"], manual, _request())
    assert updated["post_sql_refresh"]["mode"] == "manual" and len(updated["post_sql_refresh"]["views"]) == 2
    # An older client that omits the field keeps the saved setting.
    older = _flow(saved["site_id"], saved["report_id"], enabled=False, schedule_type="manual",
        sql_handoff_enabled=True, sql_mode="append", sql_database="analytics", sql_schema="public", sql_table="flow_table")
    assert older.post_sql_refresh is None
    assert flows.update_flow(saved["id"], older, _request())["post_sql_refresh"]["mode"] == "manual"
    # Disabling SQL insertion resets the refresh to Off.
    disabled = _flow(saved["site_id"], saved["report_id"], enabled=False, schedule_type="manual")
    assert flows.update_flow(saved["id"], disabled, _request())["post_sql_refresh"] == {"mode": "off", "views": []}
    with pytest.raises(ValueError):
        _flow(1, 1, post_sql_refresh={"mode": "weekly"})


def test_job_freezes_ordered_manual_list_and_recalculated_automatic_list(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    saved = _sql_flow(post_sql_refresh={"mode": "manual", "views": [view("bi", "mv_b"), view("bi", "mv_a")]})
    with database.get_db() as db:
        _seed_metadata(db)
        job = flows._build_job(db, saved["id"])
        assert job["post_sql_refresh"]["mode"] == "manual"
        assert [v["name"] for v in job["post_sql_refresh"]["views"]] == ["mv_a", "mv_b"]
        db.execute("UPDATE flows SET post_sql_refresh_json=? WHERE id=?", (json.dumps({"mode": "automatic", "views": []}), saved["id"]))
        job = flows._build_job(db, saved["id"])
        assert [v["name"] for v in job["post_sql_refresh"]["views"]] == ["mv_a", "mv_b"]
        assert job["post_sql_refresh"]["metadata_at"] == NOW and job["post_sql_refresh"]["server"] == SERVER
        # The plan follows current metadata at queue time.
        db.execute("DELETE FROM source_dependencies WHERE source_id=4")
        assert [v["name"] for v in flows._build_job(db, saved["id"])["post_sql_refresh"]["views"]] == ["mv_a"]
        db.execute("DELETE FROM source_postgres_identities WHERE source_id=1")
        blocked = flows._build_job(db, saved["id"])
        assert blocked["post_sql_refresh"]["views"] == [] and "not in the PostgreSQL dependency metadata" in blocked["post_sql_refresh"]["blocked"]
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "launched"})
    with pytest.raises(HTTPException, match="cannot run"):
        flows.queue_run(saved["id"], _request())
    with database.get_db() as db:
        db.execute("UPDATE flows SET post_sql_refresh_json=? WHERE id=?", (json.dumps({"mode": "off", "views": []}), saved["id"]))
    assert flows.queue_run(saved["id"], _request())["status"] == "queued"


def test_refresh_setting_does_not_invalidate_a_validated_recording():
    from app.flow_recordings import config_hash
    job = {"execution": {}, "downloads": {}, "sql_handoff": {"enabled": True}, "post_sql_refresh": {"mode": "manual", "views": [view("bi", "mv")]}}
    other = {**job, "post_sql_refresh": {"mode": "off", "views": []}}
    assert config_hash(job) == config_hash(other)


def test_worker_capability_gates_jobs_with_refresh_views(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "launched"})
    saved = _sql_flow(post_sql_refresh={"mode": "manual", "views": [view("bi", "mv_a")]})
    queued = flows.queue_run(saved["id"], _request())
    assert [v["name"] for v in queued["job"]["post_sql_refresh"]["views"]] == ["mv_a"]
    flows.register_worker(flows.WorkerRegister(worker_id="worker-old", display_name="old", capabilities={"shared_flow_artifacts": True}))
    assert flows.claim_run("worker-old")["run"] is None
    flows.register_worker(flows.WorkerRegister(worker_id="worker-new", display_name="new", capabilities={"shared_flow_artifacts": True, executor.CAPABILITY: True}))
    assert flows.claim_run("worker-new")["run"]["id"] == queued["id"]


# ------------------------------------------------------- persistence + retry --

def _claimed_run(monkeypatch, **flow_overrides):
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "launched"})
    saved = _sql_flow(post_sql_refresh={"mode": "manual", "views": [view("bi", "mv_a"), view("bi", "mv_b")]}, **flow_overrides)
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(worker_id="worker-1", display_name="w1", capabilities={"shared_flow_artifacts": True, executor.CAPABILITY: True}))
    assert flows.claim_run("worker-1")["run"]["id"] == queued["id"]
    return saved, queued


def _report(run_id, status, progress, worker="worker-1"):
    return flows.update_run(worker, run_id, flows.WorkerProgress(status=status, progress=progress))


def test_progress_persists_sql_outcome_separately_from_each_view_and_supports_retry(flow_db, monkeypatch):
    saved, queued = _claimed_run(monkeypatch)
    run_id = queued["id"]
    _report(run_id, "running", {"stage": "sql_insertion", "message": "Loading"})
    _report(run_id, "running", {"stage": "sql_insertion_complete", "message": "Inserted", "rows_written": 12, "files_loaded": 1, "target": "analytics.public.flow_table"})
    views = [{**view("bi", "mv_a"), "key": "analytics|bi|mv_a", "status": "succeeded", "duration_ms": 40, "error": None, "started_at": NOW, "finished_at": NOW},
             {**view("bi", "mv_b"), "key": "analytics|bi|mv_b", "status": "failed", "duration_ms": 9, "error": "lock timeout", "started_at": NOW, "finished_at": NOW}]
    _report(run_id, "running", {"stage": "view_refresh_failed", "message": "failed", "sql_committed": True, "view_refresh": {"total": 2, "completed": 1, "current": "analytics|bi|mv_b", "views": views}})
    _report(run_id, "failed", {"stage": "failed", "message": "Materialized view refresh failed"})
    run = flows.get_run(run_id)
    assert run["sql_outcome"]["committed"] is True and run["sql_outcome"]["rows_written"] == 12
    assert [(v["name"], v["status"], v["duration_ms"]) for v in run["view_refresh"]["views"]] == [("mv_a", "succeeded", 40), ("mv_b", "failed", 9)]
    assert run["view_refresh"]["completed"] == 1 and run["view_refresh"]["sql_committed"] is True
    assert run["view_refresh"]["retry"]["status"] == "eligible"
    with database.get_db() as db:
        assert db.execute("SELECT sql_reconciliation_required FROM flows WHERE id=?", (saved["id"],)).fetchone()[0] == 0
    listed = next(item for item in flows.list_runs(limit=100) if item["id"] == run_id)
    assert listed["view_refresh"]["retry"]["status"] == "eligible"

    retried = flows.retry_run_views(run_id, _request())
    assert retried["remaining_views"] == 1 and retried["job"]["job_type"] == "view_retry"
    assert retried["job"]["view_retry"]["completed"] == ["analytics|bi|mv_a"]
    assert retried["job"]["execution"]["browser_mode"] == "headless"
    with database.get_db() as db:
        row = db.execute("SELECT trigger_type, sql_outcome_json FROM flow_runs WHERE id=?", (retried["id"],)).fetchone()
        assert row["trigger_type"] == "view_retry" and json.loads(row["sql_outcome_json"])["inherited_from_run_id"] == run_id
        assert flows.inspect_view_retry_eligibility(db, run_id)["reason_code"] == "flow_active"
    retry_run = flows.get_run(retried["id"])
    assert [v["status"] for v in retry_run["view_refresh"]["views"]] == ["succeeded", "pending"]
    assert retry_run["view_refresh"]["source_run_id"] == run_id
    # An older worker cannot claim the recovery run.
    flows.register_worker(flows.WorkerRegister(worker_id="worker-old", display_name="old", capabilities={"shared_flow_artifacts": True}))
    assert flows.claim_run("worker-old")["run"] is None
    assert flows.claim_run("worker-1")["run"]["id"] == retried["id"]
    _report(retried["id"], "succeeded", {"stage": "complete", "sql_committed": True, "view_refresh": {"total": 2, "completed": 2, "views": [
        {**views[0]}, {**views[1], "status": "succeeded", "error": None}]}})
    finished = flows.get_run(retried["id"])
    assert finished["view_refresh"]["completed"] == 2 and finished["view_refresh"]["retry"] is None
    with database.get_db() as db:
        assert db.execute("SELECT last_execution_success_at FROM flows WHERE id=?", (saved["id"],)).fetchone()[0] is None


def test_retry_is_rejected_without_a_confirmed_commit_or_after_a_newer_sql_write(flow_db, monkeypatch):
    saved, queued = _claimed_run(monkeypatch)
    run_id = queued["id"]
    _report(run_id, "running", {"stage": "sql_insertion", "message": "Loading"})
    _report(run_id, "failed", {"stage": "sql_failed", "message": "copy failed", "outcome": "PostgreSQL confirmed rollback. No SQL changes were committed."})
    with database.get_db() as db:
        result = flows.inspect_view_retry_eligibility(db, run_id)
        assert result["reason_code"] == "sql_not_committed"
        assert json.loads(db.execute("SELECT sql_outcome_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()[0])["committed"] is False
    with pytest.raises(HTTPException, match="did not commit"):
        flows.retry_run_views(run_id, _request())
    # A committed run with a failed view is superseded once a newer run commits SQL.
    second = flows.queue_run(saved["id"], _request())
    assert flows.claim_run("worker-1")["run"]["id"] == second["id"]
    _report(second["id"], "running", {"stage": "sql_insertion_complete", "rows_written": 1, "files_loaded": 1})
    _report(second["id"], "running", {"stage": "view_refresh_failed", "sql_committed": True, "view_refresh": {"total": 2, "completed": 0, "views": [
        {**view("bi", "mv_a"), "status": "failed", "error": "boom"}, {**view("bi", "mv_b"), "status": "skipped"}]}})
    _report(second["id"], "failed", {"stage": "failed", "message": "view failed"})
    third = flows.queue_run(saved["id"], _request())
    assert flows.claim_run("worker-1")["run"]["id"] == third["id"]
    _report(third["id"], "running", {"stage": "sql_insertion_complete", "rows_written": 1, "files_loaded": 1})
    _report(third["id"], "succeeded", {"stage": "complete"})
    with database.get_db() as db:
        assert flows.inspect_view_retry_eligibility(db, second["id"])["reason_code"] == "superseded"
        assert flows.inspect_view_retry_eligibility(db, third["id"])["reason_code"] == "views_complete"


def test_replace_mode_never_sets_reconciliation_even_when_commit_status_is_uncertain(flow_db, monkeypatch):
    from test_flow_recordings import draft_job
    saved, job = draft_job()
    job["sql_handoff"]["enabled"] = True
    with database.get_db() as db:
        job["sql_handoff"]["mode"] = "replace"
        for events, expected in ((["sql_insertion"], 0), (["sql_insertion", "sql_insertion_complete", "view_refresh_failed"], 0)):
            db.execute("UPDATE flows SET sql_reconciliation_required=0 WHERE id=?", (saved["id"],))
            run = db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','running',?,'2026-09-07')", (saved["id"], json.dumps(job))).lastrowid
            for stage in events:
                db.execute("INSERT INTO flow_run_events(run_id,status,stage,created_at) VALUES (?,'running',?,'2026-09-07')", (run, stage))
            db.execute("UPDATE flow_runs SET status='failed' WHERE id=?", (run,))
            assert db.execute("SELECT sql_reconciliation_required FROM flows WHERE id=?", (saved["id"],)).fetchone()[0] == expected


def test_activity_steps_show_sql_insertion_then_view_refresh(flow_db, monkeypatch):
    saved, queued = _claimed_run(monkeypatch)
    run_id = queued["id"]
    with database.get_db() as db:
        run = db.execute("SELECT id,status,worker_id,job_json,progress_json,artifact_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()
        labels = [phase["label"] for phase in flow_activity.row_progress(db, run)["phases"]]
    assert labels[-3:] == ["Insert into SQL", "Refresh materialized views", "Finish"]
    _report(run_id, "running", {"stage": "sql_insertion_complete", "rows_written": 1, "files_loaded": 1})
    _report(run_id, "running", {"stage": "view_refresh", "message": "SQL insertion committed → Refreshing materialized views (2 of 2): analytics.bi.mv_b", "sql_committed": True,
                                "view_refresh": {"total": 2, "completed": 1, "views": [{**view("bi", "mv_a"), "status": "succeeded"}, {**view("bi", "mv_b"), "status": "running"}]}})
    with database.get_db() as db:
        run = db.execute("SELECT id,status,worker_id,job_json,progress_json,artifact_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()
        progress = flow_activity.row_progress(db, run)
    refresh = next(phase for phase in progress["phases"] if phase["label"] == "Refresh materialized views")
    assert (refresh["completed"], refresh["total"]) == (1, 2) and "2 of 2" in progress["message"]


# ---------------------------------------------------------------- execution --

def test_execute_flow_runs_refresh_only_after_confirmed_commit_and_retry_skips_insertion(flow_db, tmp_path, monkeypatch):
    _, job = local_job(tmp_path)
    monkeypatch.setenv("DG_FLOW_LOCK_ROOT", str(tmp_path / "locks"))
    job["sql_handoff"]["enabled"] = True
    job["post_sql_refresh"] = {"mode": "manual", "views": [view("bi", "mv_a"), view("bi", "mv_b")], "discovered_at": NOW}
    events = []
    engines = FakeEngines(fail={"mv_b"}, kinds={"bi.mv_a": "m", "bi.mv_b": "m"})
    monkeypatch.setattr(executor.flow_sql, "_engine", engines)
    from app import flow_sql
    calls = []
    def load(items, target, **kwargs):
        calls.append("sql")
        return {"rows_written": 1, "files_loaded": 1, "target": "analytics.public.flow_table"}
    monkeypatch.setattr(flow_sql, "load_artifacts", load)
    with pytest.raises(executor.ViewRefreshError):
        standalone.run(job)
    log = sorted((Path(job["paths"]["flow_folder"]) / "Scripts" / "standalone-logs").glob("*.jsonl"))[-1]
    stages = [json.loads(line)["progress"]["stage"] for line in log.read_text().splitlines()]
    assert stages.index("view_refresh_precheck") < stages.index("sql_insertion") < stages.index("sql_insertion_complete") < stages.index("view_refresh")
    assert stages[-1] == "failed" and calls == ["sql"]
    checkpoint = executor.checkpoint_path(job)
    assert executor.read_checkpoint(checkpoint) == ["analytics|bi|mv_a"]
    with pytest.raises(RuntimeError, match="retry-views"):
        standalone.run(job)
    # --no-sql skips both insertion and refresh, and never touches the checkpoint.
    calls.clear(); engines.events.clear()
    standalone.run(job, sql=False)
    assert calls == [] and not any("REFRESH" in s for _, s in engines.events) and checkpoint.is_file()
    engines.fail.clear()
    result = standalone.run(job, retry_views=True)
    assert result["status"] == "succeeded" and calls == [] and not checkpoint.exists()
    refreshed = [s for _, s in engines.events if "REFRESH" in s]
    assert len(refreshed) == 1 and "mv_b" in refreshed[0]


def test_precheck_failure_stops_before_any_sql_insertion(flow_db, tmp_path, monkeypatch):
    _, job = local_job(tmp_path)
    monkeypatch.setenv("DG_FLOW_LOCK_ROOT", str(tmp_path / "locks"))
    job["sql_handoff"]["enabled"] = True
    job["post_sql_refresh"] = {"mode": "manual", "views": [view("bi", "plain_view")]}
    monkeypatch.setattr(executor.flow_sql, "_engine", FakeEngines(kinds={"bi.plain_view": "v"}))
    from app import flow_sql
    monkeypatch.setattr(flow_sql, "load_artifacts", lambda *a, **k: pytest.fail("SQL insertion must not start"))
    with pytest.raises(RuntimeError, match="not a materialized view"):
        standalone.run(job)


def test_scripts_print_the_frozen_list_and_refuse_blocked_plans(flow_db, tmp_path, capsys):
    _, job = local_job(tmp_path)
    job["sql_handoff"]["enabled"] = True
    job["post_sql_refresh"] = {"mode": "automatic", "views": [view("bi", "mv_a")], "discovered_at": NOW, "metadata_at": NOW}
    standalone.generate(job)
    assert standalone.offline_main(job, ["--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["refresh_views"] == ["analytics.bi.mv_a"] and out["refresh_mode"] == "automatic" and out["refresh_frozen_at"] == NOW
    assert standalone.offline_main(job, ["--dry-run", "--no-sql"]) == 0
    assert json.loads(capsys.readouterr().out)["refresh_views"] == []
    from app.flow_portable import execution_sources
    assert "flow_view_refresh" in execution_sources("catalog") and "flow_view_refresh" in execution_sources("recorded")
    job["post_sql_refresh"] = {"mode": "automatic", "views": [], "blocked": "table missing from metadata"}
    assert standalone.offline_main(job, []) == 1
    assert "blocked" in capsys.readouterr().err


def test_recorded_script_dry_run_lists_refresh_views_and_no_sql_hides_them(flow_db, capsys):
    from test_flow_recordings import draft_job
    from app.flow_recording_runtime import standalone_main
    _, job = draft_job()
    job["sql_handoff"]["enabled"] = True
    job["post_sql_refresh"] = {"mode": "manual", "views": [view("bi", "mv_a")], "discovered_at": NOW}
    assert standalone_main(job, ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["refresh_views"] == ["analytics.bi.mv_a"]
    assert standalone_main(job, ["--dry-run", "--no-sql"]) == 0
    assert json.loads(capsys.readouterr().out)["refresh_views"] == []


# ---------------------------------------------------------------- pipelines --

def test_pipeline_plan_includes_flow_views_once_and_child_runs_defer(flow_db, monkeypatch):
    from test_pipelines import _seed_pipeline
    _seed_pipeline(flow_db, monkeypatch)
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    with database.get_db() as db:
        db.execute("INSERT INTO sources(id, name, type, archived) VALUES (30, 'bi_reporting.extra_mv', 'postgresql', 0)")
        upsert_postgres_identity(db, source_id=30, server=SERVER, database="analytics", schema="bi_reporting",
                                 relation="extra_mv", relation_kind="materialized_view", verified_at=NOW)
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (30, 10)")
        # Flow 20 (inflow) asks for the pipeline's own MV plus a downstream one.
        db.execute("UPDATE flows SET post_sql_refresh_json=? WHERE id=20", (json.dumps({"mode": "manual", "views": [
            view("bi_reporting", "extra_mv"), view("bi_reporting", "inflow_outflow_mv")]}),))
        db.execute("UPDATE flows SET post_sql_refresh_json=? WHERE id=21", (json.dumps({"mode": "manual", "views": [view("bi_reporting", "inflow_outflow_mv")]}),))
    plan = pipelines.build_refresh_plan(1, "Report Owner", probe_mvs=False)
    names = [mv["relation"] for mv in plan["materialized_views"]]
    assert names == ["inflow_outflow_mv", "extra_mv"]
    assert plan["materialized_views"][1]["requested_by_flow"] == "inflow"
    assert ("mv", f"{SERVER}|analytics|bi_reporting|extra_mv") in pipelines._resource_specs(plan)
    # A child run queued by the pipeline defers its configured views to the parent.
    child = _sql_flow(post_sql_refresh={"mode": "manual", "views": [view("bi_reporting", "extra_mv")]})
    with database.get_db() as db:
        run_id, job = flows.queue_flow_run_service(db, child["id"], requested_by="pipeline", trigger_type="pipeline")
        assert job["post_sql_refresh"]["deferred_to_pipeline"] is True and job["post_sql_refresh"]["views"] == []
        assert [v["name"] for v in job["post_sql_refresh"]["deferred_views"]] == ["extra_mv"]
        assert executor.plan_views(job) == []
        db.execute("DELETE FROM flow_runs WHERE id=?", (run_id,))
    # A pipeline lock on the view blocks a direct Flow run that refreshes it.
    with database.get_db() as db:
        db.execute("INSERT INTO pipeline_runs(id, report_id, status, stage, plan_hash, plan_json) VALUES (77, 1, 'running_flows', 'running_flows', 'h', '{}')")
        db.execute("INSERT INTO pipeline_resource_locks(resource_type, resource_key, run_id) VALUES ('mv', ?, 77)", (f"{SERVER}|analytics|bi_reporting|extra_mv",))
        job = flows._build_job(db, child["id"])
        with pytest.raises(HTTPException, match="reserved by full-pipeline run #77"):
            flows._assert_refresh_plan_runnable(db, job)


def test_active_flow_view_refresh_blocks_pipeline_preview(flow_db, monkeypatch):
    from test_pipelines import _seed_pipeline
    _seed_pipeline(flow_db, monkeypatch)
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    with database.get_db() as db:
        job = {"flow": {"id": 22, "name": "unrelated"}, "sql_handoff": {"enabled": True, "database": "analytics", "schema": "bi_reporting", "table": "unrelated"},
               "post_sql_refresh": {"mode": "manual", "server": SERVER, "views": [view("bi_reporting", "inflow_outflow_mv")]}}
        db.execute("INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at) VALUES (22, 'manual', 'running', ?, ?)", (json.dumps(job), NOW))
    plan = pipelines.build_refresh_plan(1, "Report Owner", probe_mvs=False)
    assert any("is being refreshed by Flow 'unrelated'" in item for item in plan["blockers"])
