"""Regression tests for the Flows API audit findings.

Everything here is synthetic: a temporary SQLite database, fictional portals
and people, and monkeypatched worker launches. No portal, PostgreSQL server,
Outlook or work PC is contacted.
"""
import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app import database, flow_paths
from app.routers import flows
from test_flows import _flow, _mark_discovered, _report, _request, _seed_catalog, _site, flow_db  # noqa: F401

# Saving a Flow here is set-up, not the subject: see skip_flow_files in conftest.py.
pytestmark = pytest.mark.usefixtures("skip_flow_files")

DUE = "2000-01-01T00:00:00"


@pytest.fixture
def quiet_worker(flow_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flows, "launch_local_worker", lambda *a, **k: {"status": "launched"})


def _three_due_flows():
    """A healthy Flow on each side of the one a test then breaks."""
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    first = flows.create_flow(_flow(site["id"], report["id"], name="Healthy before"), _request())
    broken = flows.create_flow(_flow(site["id"], report["id"], name="Broken"), _request())
    last = flows.create_flow(_flow(site["id"], report["id"], name="Healthy after"), _request())
    with database.get_db() as db:
        db.execute("UPDATE flows SET next_run_at=?", (DUE,))
    return first, broken, last


def _flow_row(flow_id):
    with database.get_db() as db:
        return dict(db.execute("SELECT * FROM flows WHERE id=?", (flow_id,)).fetchone())


def _queued_flow_ids():
    with database.get_db() as db:
        return [row[0] for row in db.execute("SELECT flow_id FROM flow_runs ORDER BY id")]


# ------------------------------------------------------------------- F-01 --

def test_f01_scheduler_skips_a_flow_whose_week_does_not_exist(quiet_worker):
    first, broken, last = _three_due_flows()
    with database.get_db() as db:
        # 2025 has no ISO week 53; the row predates the save-time check.
        db.execute("UPDATE flows SET period_strategy='rolling', window_weeks=1, start_week='2025-W53', "
                   "end_week=NULL WHERE id=?", (broken["id"],))

    result = flows.queue_due_flows()

    assert result["count"] == 2
    assert _queued_flow_ids() == [first["id"], last["id"]]
    skipped = _flow_row(broken["id"])
    assert "start week does not exist" in skipped["last_error"]
    assert skipped["last_error"].startswith("The scheduled run was skipped: ")
    # The occurrence is skipped like a 409: the schedule moves to its next run.
    assert skipped["next_run_at"] > flows._iso(flows._now())
    assert flows.queue_due_flows()["count"] == 0


def test_f01_scheduler_skips_a_flow_on_an_unknown_adapter(quiet_worker):
    first, _unused, last = _three_due_flows()
    other_site = flows.create_site(flows.SiteWrite(name="Other portal", base_url="https://other.example.test"), _request())
    other_report = flows.create_report(_report(other_site["id"]), _request())
    stranded = flows.create_flow(_flow(other_site["id"], other_report["id"], name="Stranded"), _request())
    with database.get_db() as db:
        db.execute("UPDATE flow_sites SET adapter='retired_portal' WHERE id=?", (other_site["id"],))
        db.execute("UPDATE flows SET next_run_at=? WHERE id=?", (DUE, stranded["id"]))

    flows.queue_due_flows()

    queued = _queued_flow_ids()
    assert first["id"] in queued and last["id"] in queued and stranded["id"] not in queued
    assert "Unsupported Flow source adapter" in _flow_row(stranded["id"])["last_error"]


@pytest.mark.parametrize("failure, expected", [
    (HTTPException(422, "This recording cannot run yet: step 3 has no selector"), "step 3 has no selector"),
    (RuntimeError("unexpected builder failure"), "unexpected builder failure"),
    (KeyError("window_weeks"), "window_weeks"),
])
def test_f01_scheduler_rolls_back_only_the_failing_flow(quiet_worker, monkeypatch, caplog, failure, expected):
    first, broken, last = _three_due_flows()
    build = flows._build_job

    def half_written(db, flow_id, **kwargs):
        if flow_id == broken["id"]:
            db.execute("UPDATE flows SET filename_template='half-written.csv' WHERE id=?", (flow_id,))
            raise failure
        return build(db, flow_id, **kwargs)
    monkeypatch.setattr(flows, "_build_job", half_written)

    with caplog.at_level("ERROR", logger=flows.__name__):
        result = flows.queue_due_flows()

    assert result["count"] == 2 and _queued_flow_ids() == [first["id"], last["id"]]
    skipped = _flow_row(broken["id"])
    assert skipped["filename_template"] == broken["filename_template"]  # Rolled back to the savepoint.
    assert expected in skipped["last_error"]
    assert skipped["next_run_at"] > flows._iso(flows._now())
    assert any(f"Scheduled Flow {broken['id']} could not be queued" in record.getMessage() and record.exc_info
               for record in caplog.records)


def test_f01_scheduler_keeps_the_existing_409_skip_unchanged(quiet_worker, monkeypatch):
    first, broken, last = _three_due_flows()
    build = flows._build_job

    def refused(db, flow_id, **kwargs):
        if flow_id == broken["id"]:
            raise HTTPException(409, "Flow folder must be inside the managed root.")
        return build(db, flow_id, **kwargs)
    monkeypatch.setattr(flows, "_build_job", refused)

    assert flows.queue_due_flows()["count"] == 2
    skipped = _flow_row(broken["id"])
    assert skipped["last_error"] == "Flow folder must be inside the managed root."
    assert skipped["next_run_at"] > flows._iso(flows._now())


def test_f01_scheduler_stops_retrying_a_schedule_it_cannot_read(quiet_worker):
    first, broken, last = _three_due_flows()
    with database.get_db() as db:
        db.execute("UPDATE flows SET schedule_time='25:99' WHERE id=?", (broken["id"],))

    assert flows.queue_due_flows()["count"] == 2
    skipped = _flow_row(broken["id"])
    assert skipped["next_run_at"] is None
    assert "valid HH:MM time" in skipped["last_error"] and "Save the Flow again" in skipped["last_error"]
    assert _queued_flow_ids() == [first["id"], last["id"]]


@pytest.mark.parametrize("strategy", ["rolling", "latest"])
def test_f01_saving_a_flow_rejects_a_start_week_that_does_not_exist(strategy):
    with pytest.raises(ValueError, match="start week does not exist"):
        _flow(1, 1, period_strategy=strategy, start_week="2025-W53", end_week=None)
    # 2026 does have an ISO week 53.
    assert _flow(1, 1, period_strategy=strategy, start_week="2026-W53", end_week=None).start_week == "2026-W53"


def test_f01_a_website_cannot_be_saved_on_an_unknown_adapter(flow_db):  # noqa: F811
    with pytest.raises(ValueError, match="website type Metronome supports"):
        flows.SiteWrite(name="Mystery", adapter="retired_portal", base_url="https://mystery.example.test")
    for adapter in flow_paths.SOURCE_FOLDERS:
        assert flows.SiteWrite(name="Known", adapter=adapter).adapter == adapter
    from fastapi.testclient import TestClient
    from app import main
    site = flows.create_site(_site(), _request())
    response = TestClient(main.app).put(f"/api/flows/sites/{site['id']}", json={
        "name": site["name"], "adapter": "retired_portal", "base_url": site["base_url"], "auth_url": site["auth_url"],
    })
    assert response.status_code == 422
    with database.get_db() as db:
        assert db.execute("SELECT adapter FROM flow_sites WHERE id=?", (site["id"],)).fetchone()[0] == "web_export"


def test_f01_manual_run_of_an_unbuildable_flow_answers_409_with_the_reason(quiet_worker):
    _first, broken, _last = _three_due_flows()
    with database.get_db() as db:
        db.execute("UPDATE flows SET period_strategy='rolling', window_weeks=1, start_week='2025-W53', "
                   "end_week=NULL WHERE id=?", (broken["id"],))
    with pytest.raises(HTTPException) as refused:
        flows.queue_run(broken["id"], _request())
    assert refused.value.status_code == 409
    assert refused.value.detail == ("The current Flow configuration cannot be queued: "
                                    "The selected start week does not exist in that year.")
    assert _queued_flow_ids() == []


# ------------------------------------------------------- SQL run fixtures --

SERVER = "warehouse.example.test"
WORKER = "audit-worker"
VIEWS = [{"database": "analytics", "schema": "bi", "name": "mv_a"}, {"database": "analytics", "schema": "bi", "name": "mv_b"}]


@pytest.fixture
def sql_runs(quiet_worker, tmp_path, monkeypatch):
    """Queue, claim and report SQL Flow runs the way a worker would."""
    from app import flow_sql, flow_view_refresh
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    monkeypatch.setattr(flows, "notify_flow_owner_of_failure", lambda run_id: None)
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute("""INSERT INTO flow_sql_catalog(database_name, schema_name, table_name, last_seen_at, stale)
                      VALUES ('analytics', 'public', 'flow_table', '2026-10-19T00:00:00+00:00', 0)""")
    flows.register_worker(flows.WorkerRegister(worker_id=WORKER, display_name="Audit worker", capabilities={
        "shared_flow_artifacts": True, flow_view_refresh.CAPABILITY: True,
        flow_sql.PERSON_CREDENTIALS_CAPABILITY: True, flow_sql.OWNERSHIP_CAPABILITY: True,
    }))

    class Runs:
        def flow(self, name="SQL load", **overrides):
            values = dict(name=name, enabled=False, schedule_type="manual", sql_handoff_enabled=True,
                          sql_mode="append", sql_database="analytics", sql_schema="public", sql_table="flow_table")
            return flows.create_flow(_flow(site["id"], report["id"], **{**values, **overrides}), _request())

        def report(self, run_id, status, stage, **progress):
            artifact = tmp_path / f"run-{run_id}.csv"
            artifact.write_text("value\n1\n", encoding="utf-8")
            return flows.update_run(WORKER, run_id, flows.WorkerProgress(
                status=status, progress={"stage": stage, **progress},
                artifacts=[{"file_path": str(artifact), "filename": artifact.name, "status": "saved", "row_count": 1}],
                error="synthetic failure" if status == "failed" else None,
            ))

        def claim(self, run_id):
            assert flows.claim_run(WORKER)["run"]["id"] == run_id
            return run_id

        def finish(self, flow_id, *stages, status="failed"):
            """One manual run that reports ``stages`` and then ends."""
            run_id = self.claim(flows.queue_run(flow_id, _request())["id"])
            for stage in stages:
                self.report(run_id, "running", stage)
            self.report(run_id, status, "failed" if status == "failed" else "complete")
            return run_id

        def person(self, name, sql_username=None, password=None):
            with database.get_db() as db:
                person_id = db.execute("INSERT INTO people(name, role, sql_username) VALUES (?, 'BI', ?)",
                                       (name, sql_username)).lastrowid
            if password:
                monkeypatch.setenv(flow_sql.person_password_setting(person_id), password)
            return person_id

        def assign(self, flow_id, person_id):
            flows.patch_flow(flow_id, flows.FlowInlineWrite(owner_person_id=person_id), _request())

    return Runs()


def _run_count():
    with database.get_db() as db:
        return db.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0]


def _retry_sql_reason(run_id):
    with database.get_db() as db:
        return flows.inspect_sql_retry_eligibility(db, run_id)["reason_code"]


def _refused(call, *args):
    with pytest.raises(HTTPException) as caught:
        call(*args)
    assert caught.value.status_code == 409
    return caught.value.detail


# ------------------------------------------------------------------- F-03 --

def test_f03_retry_sql_refuses_a_run_whose_own_sql_already_committed(sql_runs):
    saved = sql_runs.flow()
    committed = sql_runs.finish(saved["id"], "sql_insertion", "sql_insertion_complete")
    assert flows.get_run(committed)["sql_outcome"]["committed"] is True
    before = _run_count()

    detail = _refused(flows.retry_run_sql, committed, _request())

    assert "already committed in this run" in detail and "second time" in detail
    assert _retry_sql_reason(committed) == "sql_already_committed"
    assert _run_count() == before


def test_f03_retry_sql_refuses_an_older_run_once_a_newer_run_committed(sql_runs):
    saved = sql_runs.flow()
    older = sql_runs.finish(saved["id"], "transformation")
    newer = sql_runs.finish(saved["id"], "sql_insertion", "sql_insertion_complete", status="succeeded")
    before = _run_count()

    detail = _refused(flows.retry_run_sql, older, _request())

    assert f"A newer run (#{newer}) already committed SQL" in detail and "Use Run" in detail
    assert _retry_sql_reason(older) == "sql_superseded"
    assert _run_count() == before


def test_f03_retry_sql_cannot_load_the_same_file_twice(sql_runs):
    saved = sql_runs.flow()
    source = sql_runs.finish(saved["id"], "transformation")
    retried = flows.retry_run_sql(source, _request())
    sql_runs.claim(retried["id"])
    sql_runs.report(retried["id"], "running", "sql_insertion_complete", rows_written=1, files_loaded=1)
    sql_runs.report(retried["id"], "succeeded", "complete")

    detail = _refused(flows.retry_run_sql, source, _request())

    assert f"A newer run (#{retried['id']}) already committed SQL" in detail
    assert _retry_sql_reason(source) == "sql_superseded"


def test_f03_retry_sql_stays_available_while_nothing_committed_since(sql_runs):
    saved = sql_runs.flow()
    committed = sql_runs.finish(saved["id"], "sql_insertion", "sql_insertion_complete", status="succeeded")
    source = sql_runs.finish(saved["id"], "transformation")
    # A later run that never reached SQL, and one whose commit is unknown
    # (left exactly as before this fix), do not block the source.
    sql_runs.finish(saved["id"], "transformation")
    sql_runs.finish(saved["id"], "sql_insertion")
    with database.get_db() as db:
        unknown = db.execute("SELECT sql_outcome_json FROM flow_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        # A refresh-only retry copies the commit it inherited from an earlier
        # run; it loads nothing itself, so it is not a newer commit.
        db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, sql_outcome_json, created_at)
               VALUES (?, 'view_retry', 'succeeded', ?, ?, '2026-10-19T10:00:00')""",
            (saved["id"], json.dumps({"job_type": "view_retry"}),
             json.dumps({"committed": True, "inherited_from_run_id": committed})),
        )
    assert json.loads(unknown)["committed"] is None
    assert _retry_sql_reason(source) == "sql_artifacts_ready"
    assert flows.retry_run_sql(source, _request())["job"]["job_type"] == "sql_retry"


# ------------------------------------------------------------------- F-04 --

def _sql_target(run_id):
    with database.get_db() as db:
        return json.loads(db.execute("SELECT job_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()[0])["sql_handoff"]


def test_f04_retry_sql_fails_closed_for_a_flow_assigned_after_the_failure(sql_runs):
    saved = sql_runs.flow()
    source = sql_runs.finish(saved["id"], "transformation")
    assert "connect_as" not in _sql_target(source)  # Unassigned when it failed.
    before = _run_count()

    sql_runs.assign(saved["id"], sql_runs.person("Omar", "omar_sql"))
    detail = _refused(flows.retry_run_sql, source, _request())
    assert detail.startswith("The SQL password for Omar (omar_sql) is not saved on this computer.")

    sql_runs.assign(saved["id"], sql_runs.person("Lina"))
    detail = _refused(flows.retry_run_sql, source, _request())
    assert detail.startswith("Lina has no SQL username in Users.")
    assert _run_count() == before


def test_f04_retry_sql_signs_in_as_the_flow_owner_of_today(sql_runs):
    saved = sql_runs.flow()
    source = sql_runs.finish(saved["id"], "transformation")
    omar = sql_runs.person("Omar", "omar_sql", password="fictional-omar-password")
    sql_runs.assign(saved["id"], omar)

    retried = flows.retry_run_sql(source, _request())

    target = _sql_target(retried["id"])
    assert target["connect_as"] == {"person_id": omar, "name": "Omar", "username": "omar_sql"}
    assert target["owner_username"] == "omar_sql"
    with database.get_db() as db:
        stored = db.execute("SELECT job_json FROM flow_runs WHERE id=?", (retried["id"],)).fetchone()[0]
    assert "fictional-omar-password" not in stored
    # Everything but the identity is still what the source run froze.
    assert {key: value for key, value in target.items() if key not in {"connect_as", "owner_username"}} \
        == _sql_target(source)


def test_f04_retry_sql_never_runs_as_the_previous_owner(sql_runs):
    maya = sql_runs.person("Maya", "maya_sql", password="fictional-maya-password")
    saved = sql_runs.flow(owner_person_id=maya)
    source = sql_runs.finish(saved["id"], "transformation")
    assert _sql_target(source)["connect_as"]["username"] == "maya_sql"

    sql_runs.assign(saved["id"], sql_runs.person("Omar", "omar_sql"))
    assert "SQL password for Omar" in _refused(flows.retry_run_sql, source, _request())

    # Unassigned again: the shared account, with no trace of the old owner.
    sql_runs.assign(saved["id"], None)
    target = _sql_target(flows.retry_run_sql(source, _request())["id"])
    assert "connect_as" not in target and "owner_username" not in target


def test_f04_retry_sql_of_a_still_unassigned_flow_keeps_the_shared_account(sql_runs):
    saved = sql_runs.flow()
    source = sql_runs.finish(saved["id"], "transformation")
    sql_runs.person("Omar", "omar_sql")  # Exists, but does not own this Flow.

    target = _sql_target(flows.retry_run_sql(source, _request())["id"])

    assert "connect_as" not in target and "owner_username" not in target
    assert target == _sql_target(source)


def test_f04_retry_views_follows_the_flow_owner_of_today(sql_runs):
    saved = sql_runs.flow(post_sql_refresh={"mode": "manual", "views": VIEWS})
    source = sql_runs.claim(flows.queue_run(saved["id"], _request())["id"])
    sql_runs.report(source, "running", "sql_insertion_complete", rows_written=1, files_loaded=1)
    sql_runs.report(source, "running", "view_refresh_failed", sql_committed=True, view_refresh={
        "total": 2, "completed": 1,
        "views": [{**VIEWS[0], "status": "succeeded"}, {**VIEWS[1], "status": "failed", "error": "lock timeout"}],
    })
    sql_runs.report(source, "failed", "failed")
    assert "connect_as" not in _sql_target(source)
    before = _run_count()

    sql_runs.assign(saved["id"], sql_runs.person("Omar", "omar_sql"))
    assert "SQL password for Omar" in _refused(flows.retry_run_views, source, _request())
    assert _run_count() == before

    lina = sql_runs.person("Lina", "lina_sql", password="fictional-lina-password")
    sql_runs.assign(saved["id"], lina)
    retried = flows.retry_run_views(source, _request())
    assert _sql_target(retried["id"])["connect_as"] == {"person_id": lina, "name": "Lina", "username": "lina_sql"}
    assert retried["remaining_views"] == 1


# ------------------------------------------------------------------- F-08 --

SCHEDULED = dict(enabled=True, schedule_type="daily", schedule_time="08:00")
AUTOMATIC = {"mode": "automatic", "views": []}  # No dependency metadata is seeded, so discovery is blocked.


def _make_due(*flow_ids):
    with database.get_db() as db:
        for flow_id in flow_ids:
            db.execute("UPDATE flows SET next_run_at=? WHERE id=?", (DUE, flow_id))


def _second_sql_table():
    with database.get_db() as db:
        db.execute("""INSERT INTO flow_sql_catalog(database_name, schema_name, table_name, last_seen_at, stale)
                      VALUES ('analytics', 'public', 'other_table', '2026-10-19T00:00:00+00:00', 0)""")


def test_f08_scheduler_skips_a_flow_whose_view_refresh_is_blocked(sql_runs):
    blocked = sql_runs.flow(name="Blocked refresh", post_sql_refresh=AUTOMATIC, **SCHEDULED)
    _second_sql_table()
    healthy = sql_runs.flow(name="Healthy", sql_table="other_table", **SCHEDULED)
    with database.get_db() as db:
        assert flows._build_job(db, blocked["id"])["post_sql_refresh"]["blocked"]
    _make_due(blocked["id"], healthy["id"])

    result = flows.queue_due_flows()

    assert result["count"] == 1 and _queued_flow_ids() == [healthy["id"]]
    skipped = _flow_row(blocked["id"])
    assert skipped["last_error"].startswith("Refresh materialized views cannot run: ")
    assert skipped["next_run_at"] > flows._iso(flows._now())
    # The same answer a manual Run already gave.
    assert _refused(flows.queue_run, blocked["id"], _request()) == skipped["last_error"]


def test_f08_scheduler_still_queues_two_flows_that_refresh_the_same_view(sql_runs):
    """Left as before on purpose: a skipped scheduled occurrence has nobody to retry it."""
    first = sql_runs.flow(name="First", post_sql_refresh={"mode": "manual", "views": VIEWS[:1]}, **SCHEDULED)
    _second_sql_table()
    second = sql_runs.flow(name="Second", sql_table="other_table",
                           post_sql_refresh={"mode": "manual", "views": VIEWS[:1]}, **SCHEDULED)
    _make_due(first["id"], second["id"])

    assert flows.queue_due_flows()["count"] == 2
    assert _queued_flow_ids() == [first["id"], second["id"]]


def test_f08_resume_refuses_a_blocked_view_refresh_plan(sql_runs):
    saved = sql_runs.flow(post_sql_refresh={"mode": "manual", "views": VIEWS[:1]})
    source = sql_runs.finish(saved["id"], "transformation")
    with database.get_db() as db:
        assert flows.inspect_resume_eligibility(db, source)["status"] == "eligible"
        db.execute("UPDATE flows SET post_sql_refresh_json=? WHERE id=?", (json.dumps(AUTOMATIC), saved["id"]))
    before = _run_count()

    detail = _refused(flows.resume_run, source, _request())

    assert detail.startswith("Refresh materialized views cannot run: ")
    assert _run_count() == before


def test_f08_resume_refuses_while_another_flow_refreshes_the_same_view(sql_runs):
    saved = sql_runs.flow(post_sql_refresh={"mode": "manual", "views": VIEWS[:1]})
    source = sql_runs.finish(saved["id"], "transformation")
    _second_sql_table()
    other = sql_runs.flow(name="Other", sql_table="other_table", post_sql_refresh={"mode": "manual", "views": VIEWS[:1]})
    active = flows.queue_run(other["id"], _request())["id"]

    detail = _refused(flows.resume_run, source, _request())

    assert detail == f"analytics.bi.mv_a is already being refreshed by Flow 'Other' run #{active}."
    flows.stop_run(other["id"], _request())
    assert flows.resume_run(source, _request())["resumes_run_id"] == source


# ------------------------------------------------------------------- F-17 --

EMAIL = {"enabled": True, "recipients": ["ops@example.test"], "subject": None}
EMAIL_OFF = {"enabled": False, "recipients": [], "subject": None}


def _python_body(script, **overrides):
    values = dict(name="Python job", source_type="python", python_scripts=[str(script)],
                  schedule_type="daily", schedule_time="08:00")
    return flows.FlowWrite(**{**values, **overrides})


@pytest.mark.parametrize("sent", [{}, {"email_delivery": EMAIL}, {"email_delivery": None}])
def test_f17_switching_to_just_run_the_scripts_clears_the_saved_email_step(flow_db, tmp_path, sent):  # noqa: F811
    script = tmp_path / "job.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    saved = flows.create_flow(_python_body(script, email_delivery=EMAIL), _request())
    assert saved["email_delivery"] == EMAIL

    updated = flows.update_flow(saved["id"], _python_body(script, python_run_mode="run", **sent), _request())

    assert updated["python_run_mode"] == "run"
    assert updated["email_delivery"] == EMAIL_OFF
    with database.get_db() as db:
        assert flows._build_job(db, saved["id"])["email_delivery"] == EMAIL_OFF


def test_f17_an_omitted_email_step_is_still_kept_for_flows_that_produce_a_file(flow_db, tmp_path):  # noqa: F811
    script = tmp_path / "job.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    saved = flows.create_flow(_python_body(script, email_delivery=EMAIL), _request())

    older_client = _python_body(script, name="Renamed by an older client")
    assert older_client.email_delivery is None
    assert flows.update_flow(saved["id"], older_client, _request())["email_delivery"] == EMAIL
    # Switching it off explicitly still works, and stays off when omitted again.
    assert flows.update_flow(saved["id"], _python_body(script, email_delivery=EMAIL_OFF), _request())["email_delivery"] == EMAIL_OFF
    assert flows.update_flow(saved["id"], _python_body(script), _request())["email_delivery"] == EMAIL_OFF


# ------------------------------------------------------------------- F-21 --

def _emailed_run(monkeypatch, *, status, finished_at):
    """One terminal run of an owned Flow with an Email step, stamped in UTC."""
    monkeypatch.setattr(flows, "launch_local_worker", lambda *a, **k: {"status": "launched"})
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        owner = db.execute("INSERT INTO people(name, role, email) VALUES ('Maya', 'BI', 'maya@example.test')").lastrowid
    saved = flows.create_flow(_flow(site["id"], report["id"], owner_person_id=owner, email_delivery=EMAIL), _request())
    run_id = flows.queue_run(saved["id"], _request())["id"]
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status=?, finished_at=?, error='synthetic failure' WHERE id=?",
                   (status, finished_at, run_id))
    return run_id


def test_f21_dubai_time_text_reads_stored_utc_and_explicit_offsets():
    from app.flow_email_delivery import dubai_time_text
    assert dubai_time_text("2026-10-19T10:05:00") == "19 Oct 2026, 14:05 (Dubai time)"
    assert dubai_time_text("2026-10-19 21:30:00") == "20 Oct 2026, 01:30 (Dubai time)"  # Next day in Dubai.
    assert dubai_time_text("2026-10-19T10:05:00Z") == "19 Oct 2026, 14:05 (Dubai time)"
    assert dubai_time_text("2026-10-19T15:35:00+05:30") == "19 Oct 2026, 14:05 (Dubai time)"
    assert dubai_time_text(None) is None and dubai_time_text("") is None
    assert dubai_time_text("not a timestamp") == "not a timestamp"


def test_f21_failure_email_shows_dubai_time_with_the_zone_named(flow_db, monkeypatch):  # noqa: F811
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(flows, "dubai_now", lambda: datetime(2026, 10, 19, 14, 7, tzinfo=ZoneInfo("Asia/Dubai")))
    run_id = _emailed_run(monkeypatch, status="failed", finished_at="2026-10-19T10:05:00")
    with database.get_db() as db:
        body = flows._flow_failure_message(flows._flow_failure_context(db, run_id))["html_body"]

    assert "Failed at" in body and "19 Oct 2026, 14:05 (Dubai time)" in body
    assert " ".join(body.split()).count("Detected by Metronome at 19 Oct 2026, 14:07 (Dubai time).") == 1
    assert "2026-10-19T10:05:00" not in body and "local time" not in body


def test_f21_flow_file_email_shows_dubai_time_with_the_zone_named(flow_db, monkeypatch):  # noqa: F811
    from app import flow_email_delivery
    run_id = _emailed_run(monkeypatch, status="succeeded", finished_at="2026-10-19T21:30:00")
    with database.get_db() as db:
        context = flow_email_delivery.run_context(db, run_id)
    body = flow_email_delivery.build_message(context, attach=True)["html_body"]

    assert "Finished at" in body and "20 Oct 2026, 01:30 (Dubai time)" in body
    assert "2026-10-19T21:30:00" not in body


# ------------------------------------------------------------------- F-23 --

def _status(call, *args):
    with pytest.raises(HTTPException) as caught:
        call(*args)
    return caught.value.status_code, caught.value.detail


def test_f23_renaming_a_website_to_a_taken_name_answers_409(flow_db):  # noqa: F811
    first = flows.create_site(_site(), _request())
    second = flows.create_site(flows.SiteWrite(name="Second portal", base_url="https://second.example.test"), _request())

    taken = flows.SiteWrite(name=first["name"], base_url="https://second.example.test")
    assert _status(flows.update_site, second["id"], taken, _request()) == (409, "A website with that name already exists.")

    with database.get_db() as db:
        assert db.execute("SELECT name FROM flow_sites WHERE id=?", (second["id"],)).fetchone()[0] == "Second portal"
    renamed = flows.SiteWrite(name="Renamed portal", base_url="https://second.example.test")
    assert flows.update_site(second["id"], renamed, _request())["name"] == "Renamed portal"
    assert _status(flows.update_site, 9999, flows.SiteWrite(name="Nobody"), _request()) == (404, "Website not found.")


def test_f23_saving_a_report_onto_a_taken_name_or_missing_website_is_refused(flow_db):  # noqa: F811
    site, report = _seed_catalog()
    other = _report(site["id"])
    other.name = "Monthly movement"
    second = flows.create_report(other, _request())

    other.name = report["name"]
    assert _status(flows.update_report, second["id"], other, _request()) == (409, "That report already exists for this website.")

    other.name, other.site_id = "Monthly movement", 9999
    assert _status(flows.update_report, second["id"], other, _request()) == (400, "Website not found.")
    assert _status(flows.update_report, 9999, other, _request()) == (404, "Report not found.")

    other.site_id = site["id"]
    other.name = "Quarterly movement"
    assert flows.update_report(second["id"], other, _request())["name"] == "Quarterly movement"
    with database.get_db() as db:
        assert db.execute("SELECT site_id FROM flow_reports WHERE id=?", (second["id"],)).fetchone()[0] == site["id"]


def test_f23_paths_page_reports_a_flow_on_an_unknown_adapter(flow_db, tmp_path):  # noqa: F811
    from app.routers import system_paths
    site, report = _seed_catalog()
    healthy = flows.create_flow(_flow(site["id"], report["id"], name="Healthy"), _request())
    other_site = flows.create_site(flows.SiteWrite(name="Other portal", base_url="https://other.example.test"), _request())
    other_report = flows.create_report(_report(other_site["id"]), _request())
    stranded = flows.create_flow(_flow(other_site["id"], other_report["id"], name="Stranded"), _request())
    with database.get_db() as db:
        db.execute("UPDATE flow_sites SET adapter='retired_portal' WHERE id=?", (other_site["id"],))

    state = system_paths.get_paths()

    assert [(item["id"], item["name"], item["reason"]) for item in state["flows_outside_root"]] == [
        (stranded["id"], "Stranded", "Unsupported Flow source adapter."),
    ]
    assert healthy["id"] not in [item["id"] for item in state["flows_outside_root"]]
    # Validating and saving the paths no longer fail on that one Flow either.
    body = system_paths.PathsWrite(flows_root=state["flows_root"])
    assert system_paths.validate_paths(body, _request())["ok"] is True
    assert stranded["id"] in [item["id"] for item in system_paths.put_paths(body, _request())["flows_outside_root"]]


def _catalog_rows():
    with database.get_db() as db:
        return {(row[0], row[1], row[2]): row[3] for row in db.execute(
            "SELECT database_name, schema_name, table_name, stale FROM flow_sql_catalog")}


def test_f23_partial_sql_catalog_scan_keeps_tables_of_the_database_it_could_not_read(flow_db, monkeypatch):  # noqa: F811
    with database.get_db() as db:
        db.executemany(
            """INSERT INTO flow_sql_catalog(database_name, schema_name, table_name, last_seen_at, stale)
               VALUES (?, ?, ?, '2026-10-18T00:00:00', ?)""",
            [("analytics", "public", "kept", 0), ("analytics", "public", "dropped", 0),
             ("finance", "public", "ledger", 0), ("finance", "public", "already_stale", 1)],
        )
    scan = {"targets": [{"database": "analytics", "schema": "public", "table": "kept"},
                        {"database": "analytics", "schema": "public", "table": "new"}],
            "database_count": 2, "duration_ms": 5, "errors": [{"database": "finance", "error": "connection timed out"}]}
    monkeypatch.setattr(flows, "discover_sql_catalog", lambda: scan)

    assert flows.refresh_sql_catalog()["status"] == "partial"

    assert _catalog_rows() == {
        ("analytics", "public", "kept"): 0, ("analytics", "public", "new"): 0, ("analytics", "public", "dropped"): 1,
        ("finance", "public", "ledger"): 0, ("finance", "public", "already_stale"): 1,
    }
    assert flows.sql_catalog()["scan"]["error"] == "finance: connection timed out"
    # A Flow that loads into the unread database can still be saved.
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site["id"], report["id"], sql_handoff_enabled=True, sql_mode="append",
                                    sql_database="finance", sql_schema="public", sql_table="ledger"), _request())
    assert saved["sql_table"] == "ledger"


def test_f23_complete_sql_catalog_scan_still_marks_everything_unseen_stale(flow_db, monkeypatch):  # noqa: F811
    with database.get_db() as db:
        db.executemany(
            """INSERT INTO flow_sql_catalog(database_name, schema_name, table_name, last_seen_at, stale)
               VALUES (?, 'public', ?, '2026-10-18T00:00:00', 0)""",
            [("analytics", "kept"), ("analytics", "dropped"), ("finance", "ledger")],
        )
    monkeypatch.setattr(flows, "discover_sql_catalog", lambda: {
        "targets": [{"database": "analytics", "schema": "public", "table": "kept"}],
        "database_count": 1, "duration_ms": 5, "errors": [],
    })

    assert flows.refresh_sql_catalog()["status"] == "succeeded"

    assert _catalog_rows() == {
        ("analytics", "public", "kept"): 0, ("analytics", "public", "dropped"): 1, ("finance", "public", "ledger"): 1,
    }


# ------------------------------------------------------------------- C-02 --

COLLEAGUE = ("203.0.113.9", 50000)
ASAP = {"name": "ASAP", "adapter": "asap_portal", "auth_url": "https://portal.example.test/login",
        "base_url": "https://portal.example.test"}
GSCM = {"name": "GSCM test portal", "adapter": "gscm_portal", "auth_url": "https://gscm.example.test/login",
        "base_url": "https://gscm.example.test"}
OTHER = {"name": "Other portal", "adapter": "web_export", "auth_url": "https://other.example.test/login",
         "base_url": "https://other.example.test"}
ELSEWHERE = "https://elsewhere.example.test/login"


@pytest.fixture
def browsers(flow_db, monkeypatch):  # noqa: F811
    """The host PC's browser and a colleague's, through the real middleware."""
    from fastapi.testclient import TestClient
    from app import main, user_identity
    monkeypatch.setattr(user_identity, "resolve_hostname", lambda ip: None)
    user_identity.clear_cache()
    stored = []
    monkeypatch.setattr(flows, "save_asap_credentials",
                        lambda username, password: stored.append(username) or {"configured": True})
    host, colleague = TestClient(main.app), TestClient(main.app, client=COLLEAGUE)
    host.stored = stored
    return host, colleague


def _host_only(response):
    assert response.status_code == 403, response.text
    assert "Only the host PC" in response.json()["detail"]


def _site_row(site_id):
    with database.get_db() as db:
        return dict(db.execute("SELECT name, adapter, auth_url, base_url FROM flow_sites WHERE id=?", (site_id,)).fetchone())


def test_c02_only_the_host_pc_saves_a_portal_sign_in(browsers):
    host, colleague = browsers
    asap = host.post("/api/flows/sites", json=ASAP).json()
    other = host.post("/api/flows/sites", json=OTHER).json()
    login = {"username": "portal.user", "password": "fictional-portal-password"}

    _host_only(colleague.post(f"/api/flows/sites/{asap['id']}/credentials", json=login))
    _host_only(colleague.post(f"/api/flows/sites/{other['id']}/credentials", json=login))
    assert host.stored == []

    saved = host.post(f"/api/flows/sites/{asap['id']}/credentials", json=login)
    assert saved.status_code == 200 and host.stored == ["portal.user"]


def test_c02_only_the_host_pc_adds_or_changes_a_website_the_worker_signs_in_to(browsers):
    host, colleague = browsers
    _host_only(colleague.post("/api/flows/sites", json=ASAP))
    _host_only(colleague.post("/api/flows/sites", json=GSCM))
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM flow_sites WHERE name IN (?, ?)",
                          (ASAP["name"], GSCM["name"])).fetchone()[0] == 0
    asap = host.post("/api/flows/sites", json=ASAP).json()
    gscm = host.post("/api/flows/sites", json=GSCM).json()

    # Pointing the stored login somewhere else, by address or by turning the
    # website into one the worker no longer treats as a portal.
    _host_only(colleague.put(f"/api/flows/sites/{asap['id']}", json={**ASAP, "auth_url": ELSEWHERE}))
    _host_only(colleague.put(f"/api/flows/sites/{gscm['id']}", json={**GSCM, "base_url": ELSEWHERE}))
    _host_only(colleague.put(f"/api/flows/sites/{asap['id']}", json={**ASAP, "adapter": "web_export"}))
    assert _site_row(asap["id"]) == ASAP and _site_row(gscm["id"]) == GSCM

    changed = host.put(f"/api/flows/sites/{asap['id']}", json={**ASAP, "auth_url": "https://portal.example.test/sso"})
    assert changed.status_code == 200 and _site_row(asap["id"])["auth_url"] == "https://portal.example.test/sso"


def test_c02_other_websites_stay_open_but_cannot_be_turned_into_a_portal(browsers):
    _host, colleague = browsers
    created = colleague.post("/api/flows/sites", json=OTHER)
    assert created.status_code == 200, created.text
    site_id = created.json()["id"]

    renamed = colleague.put(f"/api/flows/sites/{site_id}", json={**OTHER, "name": "Renamed portal"})
    assert renamed.status_code == 200 and _site_row(site_id)["name"] == "Renamed portal"

    _host_only(colleague.put(f"/api/flows/sites/{site_id}", json={**OTHER, "adapter": "asap_portal"}))
    _host_only(colleague.put(f"/api/flows/sites/{site_id}", json={**OTHER, "adapter": "gscm_portal"}))
    assert _site_row(site_id)["adapter"] == "web_export"


def test_c02_route_functions_refuse_a_colleague_the_way_other_host_only_changes_do(flow_db):  # noqa: F811
    from types import SimpleNamespace
    colleague = SimpleNamespace(state=SimpleNamespace(actor="Omar", person_id=None, is_host=False))
    asap = flows.create_site(flows.SiteWrite(**ASAP), _request())
    for call in (
        lambda: flows.create_site(flows.SiteWrite(**GSCM), colleague),
        lambda: flows.update_site(asap["id"], flows.SiteWrite(**{**ASAP, "auth_url": ELSEWHERE}), colleague),
        lambda: flows.configure_site_credentials(
            asap["id"], flows.CredentialWrite(username="portal.user", password="fictional-portal-password"), colleague),
    ):
        status, detail = _status(call)
        assert status == 403 and "Only the host PC" in detail
