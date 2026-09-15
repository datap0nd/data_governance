"""Synthetic scheduler, complete native-tool loop, and worker provenance."""
import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import database
from app.auditor import detection, engine, store
from app.auditor import config as auditor_config
from test_data_auditor import audit_db, evidence, tmp_path


def test_complete_model_loop_records_verified_drop_and_keeps_flow_unchanged(audit_db, monkeypatch):
    calls = []
    async def transport(client, method, url, token, payload=None, **kwargs):
        calls.append(url)
        if url.endswith("/catalog"):
            return {"policy_revision": "approved-policy", "datasets": [{"id":"sales", "flow_id":audit_db.flow,
                "sql_enabled":False, "runs":[{"id":r} for r in range(5,0,-1)]}]}
        if url.endswith("/inspect"):
            run = payload["run_id"]
            return evidence(run, 70000 if run == 5 else 100000, audit_db.flow)
        if sum("/chat/completions" in item for item in calls) == 1:
            return {"choices":[{"message":{"content":None,"tool_calls":[{"type":"function","id":"read-1","function":{
                "name":"read_profile","arguments":json.dumps({"dataset_id":"sales","run_id":5,"source":"download"})}}]}}]}
        candidates = json.loads(payload["messages"][1]["content"])["validated_candidates"]
        assert len(candidates) == 1
        assert payload["messages"][-1]["role"] == "tool"
        evidence_id = json.loads(payload["messages"][-1]["content"])["evidence_id"]
        return {"choices":[{"message":{"content":json.dumps({"candidate_ids":candidates,"hypotheses":[{
            "dataset_id":"sales","evidence_ids":[evidence_id],
            "summary":"Reporting pattern may have shifted.","confidence":"low"}]})}}]}
    monkeypatch.setattr(engine, "json_request", transport)
    with database.get_db() as db:
        before = tuple(db.execute("SELECT * FROM flows WHERE id=?", (audit_db.flow,)).fetchone())
    engine._run(audit_db.audit, "manual", [audit_db.flow])
    latest = store.projection()["latest"]
    assert latest["status"] == "completed"
    assert latest["coverage"]["model"] == "completed" and latest["coverage"]["findings"] == 1
    assert latest["coverage"]["model_assessment"][0]["status"] == "unconfirmed"
    with database.get_db() as db:
        assert tuple(db.execute("SELECT * FROM flows WHERE id=?", (audit_db.flow,)).fetchone()) == before
        assert db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
        assert "-30.0%" in db.execute("SELECT message FROM alerts").fetchone()[0]


def test_metric_checks_continue_when_local_ai_is_not_configured(audit_db, monkeypatch):
    async def transport(client, method, url, token, payload=None, **kwargs):
        if url.endswith("/catalog"):
            return {"catalog_revision":"approved-policy", "datasets":[{"id":"sales",
                "flow_id":audit_db.flow, "sql_enabled":False, "runs":[{"id":1}]}]}
        if url.endswith("/inspect"):
            return evidence(1, 100, audit_db.flow)
        raise AssertionError("The model endpoint must not be called")
    monkeypatch.setattr(engine, "json_request", transport)
    cfg = replace(auditor_config.settings(), model_url="", model_token="", model="")
    coverage = {"checked":0,"unverified":[],"findings":0,"model":"pending"}
    asyncio.run(engine.audit(audit_db.audit, [audit_db.flow], coverage, cfg))
    assert coverage["checked"] == 1
    assert coverage["model"] == "unavailable_not_configured"


def test_schedule_claim_is_once_only_and_manual_is_always_available(audit_db, monkeypatch):
    queued = []
    executor = SimpleNamespace(submit=lambda *args: queued.append(args) or SimpleNamespace(done=lambda: True))
    monkeypatch.setattr(engine, "_executor", executor)
    monkeypatch.setattr(engine, "_future", None)
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='completed'")
    clock = datetime(2026,9,14,22,0,tzinfo=timezone.utc)
    value = {**store.DEFAULTS,"paused":False,"time":"02:00","next_due":clock.isoformat()}
    with database.get_db() as db:
        db.execute("UPDATE auditor_settings SET value=? WHERE id=1", (json.dumps(value),))
    audit = engine.start("overnight", clock)
    assert audit is not None and len(queued) == 1
    assert engine.start("overnight", clock) is None
    with pytest.raises(ValueError, match="already active"):
        engine.start("manual", clock)
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='completed' WHERE id=?", (audit,))
    store.write_settings(value)
    assert engine.start("manual", clock) is not None
    assert len(queued) == 2


def test_long_outage_advances_without_queuing_a_backlog(audit_db, monkeypatch):
    monkeypatch.setattr(engine, "_future", None)
    clock = datetime(2026,9,15,10,0,tzinfo=timezone.utc)
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='completed'")
        value = {**store.DEFAULTS,"paused":False,"time":"02:00","next_due":(clock-timedelta(hours=12)).isoformat()}
        db.execute("UPDATE auditor_settings SET value=? WHERE id=1", (json.dumps(value),))
    assert engine.start("overnight", clock) is None
    assert store.read_settings()["next_due"] == "2026-09-15T22:00:00+00:00"


def test_coverage_notice_is_deduplicated_and_pause_blocks_late_notice(audit_db):
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='completed_with_gaps' WHERE id=?", (audit_db.audit,))
    store.coverage_alert(audit_db.audit, [audit_db.flow])
    store.coverage_alert(audit_db.audit, [audit_db.flow])
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    store.write_settings({"paused":True,"time":"02:00"})
    store.coverage_alert(audit_db.audit, [audit_db.flow, 999])
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


@pytest.mark.parametrize("changes_itself", [False, True])
def test_transformation_provenance_never_gates_the_existing_worker(tmp_path, changes_itself):
    from app import flow_worker
    source = tmp_path / "input.csv"
    source.write_text("Name,Units\nFictional,1\n", encoding="utf-8")
    script = tmp_path / "transform.py"
    code = "from pathlib import Path\nfrom argparse import ArgumentParser\np=ArgumentParser();p.add_argument('--input');p.add_argument('--output');a=p.parse_args()\nPath(a.output).write_bytes(Path(a.input).read_bytes())\n"
    if changes_itself:
        code += "Path(__file__).write_text('# synthetic change',encoding='utf-8')\n"
    script.write_text(code, encoding="utf-8")
    expected = hashlib.sha256(script.read_bytes()).hexdigest()
    result = flow_worker._run_transformations([{"file_path":str(source),"status":"saved","period_key":[]}], {"enabled":True,"script_path":str(script)})
    assert result[0]["row_count"] == 1
    assert result[0]["script_checksum"] == (None if changes_itself else expected)


def test_duplicate_dubai_dates_do_not_count_as_distinct_baseline_days(audit_db):
    times = ["2026-09-06T23:00:00+00:00", "2026-09-07T01:00:00+00:00", "2026-08-31T01:00:00+00:00", "2026-08-24T01:00:00+00:00"]
    for run, timestamp in enumerate(times,1):
        store.save_profile(audit_db.audit, evidence(run,100,finished_at=timestamp))
    assert detection.baseline(audit_db.audit, evidence(5,70,finished_at="2026-09-14T01:00:00+00:00")) is None
