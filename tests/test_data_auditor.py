"""Synthetic audit evidence, authorization, cancellation, and hostile models."""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.auditor import config, detection, engine, router, store
from auditor_reader import policy, profiles, service


@pytest.fixture
def tmp_path():
    # Ordinary directories avoid pytest's numbered "current" symlinks, which
    # packaged Windows hosts can create but cannot subsequently resolve.
    with TemporaryDirectory(prefix="auditor-fixture-") as folder:
        yield Path(folder).resolve(strict=True)


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "audit.sqlite"))
    database.init_db()
    store.init()
    monkeypatch.setattr(config, "settings", lambda: config.Config("https://reader.invalid", "r" * 40,
        "https://model.invalid/v1/chat/completions", "m" * 40, "synthetic-qwen", "o" * 40,
        str(tmp_path / "reader.sqlite"), (1,)))
    engine._cancel.clear()
    with database.get_db() as db:
        site = db.execute("INSERT INTO flow_sites(name,adapter,base_url) VALUES('Fictional','web_export','https://fixture.invalid')").lastrowid
        report = db.execute("INSERT INTO flow_reports(site_id,name,report_url) VALUES(?,'Fictional','https://fixture.invalid/report')", (site,)).lastrowid
        flow = db.execute("INSERT INTO flows(name,site_id,report_id,target_folder,filename_template) VALUES('Fictional flow',?,?,'fixture','fixture.csv')", (site, report)).lastrowid
    store.write_settings({"enabled": True, "flow_ids": [flow], "overnight": False, "time": "02:00"})
    with database.get_db() as db:
        audit = db.execute("INSERT INTO auditor_runs(status,trigger_type,started_at,selected_flows) VALUES('running','manual',?,?)", (store.now().isoformat(), json.dumps([flow]))).lastrowid
    yield SimpleNamespace(flow=flow, audit=audit, root=tmp_path)
    engine._cancel.clear()


def dataset():
    return policy.Dataset.model_validate_json(json.dumps({"id": "sales", "flow_id": 1,
        "columns": [{"name": "region", "csv_header": "Region"}, {"name": "units", "csv_header": "Units", "kind": "number"}]}))


def csv_fixture(tmp_path, content="Region,Units\nNorth,10\nSouth,20\n"):
    path = tmp_path / "final.csv"
    path.write_text(content, encoding="utf-8")
    approved = policy.Policy.model_validate_json(json.dumps({"manifest_db": str(tmp_path / "manifest.db"),
        "artifact_roots": [str(tmp_path)], "datasets": [dataset().model_dump()]}))
    artifact = {"file_path": str(path), "status": "saved", "checksum": hashlib.sha256(path.read_bytes()).hexdigest(), "row_count": 999999}
    return path, approved, artifact


def test_csv_recounts_complete_bytes_and_never_returns_raw_values(tmp_path):
    path, approved, artifact = csv_fixture(tmp_path, 'Region,Units\n"North\nbranch",10\nSouth,20\nSouth,3\n')
    before = path.read_bytes()
    result = profiles.profile_csv([artifact], dataset(), approved, b"s" * 32, lambda: False)
    assert result["rows"] == 3
    assert result["columns"]["units"]["sum"] == "33"
    assert result["columns"]["region"]["distinct"] == 2
    assert "North" not in json.dumps(result) and "South" not in json.dumps(result)
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["checksum", "outside", "link", "ragged", "cancelled", "byte_budget", "missing_column"])
def test_csv_fails_closed(tmp_path, failure):
    path, approved, artifact = csv_fixture(tmp_path)
    if failure == "checksum":
        artifact["checksum"] = "0" * 64
    elif failure == "outside":
        approved = approved.model_copy(update={"artifact_roots": (str(tmp_path / "other"),)})
    elif failure == "link":
        os.link(path, tmp_path / "alias.csv")
    elif failure == "ragged":
        path, approved, artifact = csv_fixture(tmp_path, "Region,Units\nNorth,10,unexpected\n")
    elif failure == "byte_budget":
        approved = approved.model_copy(update={"max_file_bytes": 1})
    elif failure == "missing_column":
        path, approved, artifact = csv_fixture(tmp_path, "Region,Wrong\nNorth,10\n")
    with pytest.raises(policy.InspectionError):
        profiles.profile_csv([artifact], dataset(), approved, b"s" * 32, lambda: failure == "cancelled")


def evidence(run, rows, flow=1, **changes):
    value = {"dataset_id": "sales", "flow_id": flow, "run_id": run, "source": "download",
        "finished_at": (datetime(2026, 8, 3, tzinfo=timezone.utc) + timedelta(weeks=run)).isoformat(),
        "scope": "stable-scope", "policy_revision": "approved-policy", "sql_comparable": False,
        "observed_at": store.now().isoformat(),
        "profile": {"rows": rows, "complete": True, "schema": "stable-schema", "columns": {}}}
    value.update(changes)
    return value


def test_30_percent_drop_is_pinned_deduplicated_and_not_auto_resolved(audit_db):
    for run in range(1, 5):
        store.save_profile(audit_db.audit, evidence(run, 100000, audit_db.flow))
    current = evidence(5, 70000, audit_db.flow)
    reference = detection.baseline(audit_db.audit, current)
    found = detection.detect(current, reference)
    assert [f["kind"] for f in found] == ["row_count_change"]
    assert "-30.0%" in found[0]["message"]
    store.emit(audit_db.audit, found[0])
    store.emit(audit_db.audit, found[0])
    for run in range(6, 12):
        store.save_profile(audit_db.audit, evidence(run, 70000, audit_db.flow))
    assert detection.baseline(audit_db.audit, evidence(12, 70000))["rows"] == 100000
    assert detection.detect(evidence(13, 100000), reference) == []
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM auditor_findings").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM alerts WHERE resolution_status IS NULL").fetchone()[0] == 1


def test_scope_changes_and_volatile_history_do_not_form_baselines(audit_db):
    for run, rows in enumerate([100000, 70000, 130000, 100000], 1):
        store.save_profile(audit_db.audit, evidence(run, rows, audit_db.flow))
    assert detection.baseline(audit_db.audit, evidence(5, 70000)) is None
    assert detection.baseline(audit_db.audit, evidence(5, 70000, scope="different filters")) is None


def test_sql_count_mismatch_requires_same_run_and_proven_boundary():
    current = evidence(5, 100)
    sql = evidence(5, 70, source="sql", sql_comparable=True)
    assert detection.detect(current, None, sql)[0]["kind"] == "insertion_count_mismatch"
    assert detection.detect(current, None, {**sql, "sql_comparable": False}) == []
    assert detection.detect(current, None, {**sql, "run_id": 6}) == []


def test_stop_fences_late_profiles_and_findings(audit_db):
    engine.cancel()
    candidate = evidence(5, 70, audit_db.flow)
    assert store.save_profile(audit_db.audit, candidate) is False
    store.emit(audit_db.audit, detection.finding(candidate, "row_count_change", "Synthetic", []))
    with database.get_db() as db:
        assert not db.execute("SELECT 1 FROM auditor_profiles").fetchone()
        assert not db.execute("SELECT 1 FROM alerts").fetchone()
    store.recover()
    assert store.projection()["latest"]["status"] == "interrupted"


def test_dubai_schedule_and_disable():
    value = {"enabled": True, "overnight": True, "time": "02:00"}
    before = datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)
    assert store.next_due(before, value) == "2026-09-14T22:00:00+00:00"
    assert store.next_due(before + timedelta(hours=2), value) == "2026-09-15T22:00:00+00:00"
    assert store.next_due(before, {**value, "enabled": False}) is None


def test_operator_boundaries_and_disable_during_reader_outage(audit_db, monkeypatch):
    app = FastAPI()
    app.include_router(router.router)
    client = TestClient(app, client=("127.0.0.1", 50000))
    headers = {"Authorization": "Bearer " + "o" * 40}
    assert client.get("/api/auditor/status").json()["authorized"] is False
    assert "latest" not in client.get("/api/auditor/status").json()
    for path in ["run", "stop"]:
        assert client.post("/api/auditor/" + path).status_code == 401
    assert client.get("/api/auditor/status", headers={**headers, "Origin": "https://attacker.invalid"}).json()["authorized"] is False
    remote_http = TestClient(app, base_url="http://fixture.invalid", client=("192.0.2.1", 50000))
    assert not remote_http.get("/api/auditor/status", headers=headers).json()["authorized"]
    mounted = TestClient(app, base_url="https://fixture.invalid", root_path="/metronome", client=("192.0.2.1", 50000))
    assert mounted.get("/api/auditor/status", headers={**headers, "Origin": "https://fixture.invalid"}).json()["authorized"]
    async def unavailable():
        raise ValueError("private upstream message")
    monkeypatch.setattr(engine, "read_catalog", unavailable)
    body = {"enabled": False, "flow_ids": [audit_db.flow], "overnight": True, "time": "02:00"}
    assert client.put("/api/auditor/settings", headers=headers, json=body).status_code == 200
    assert not store.read_settings()["enabled"]
    assert client.put("/api/auditor/settings", headers=headers, json={**body, "model_url": "https://attacker.invalid"}).status_code == 422
    failed = client.put("/api/auditor/settings", headers=headers, json={**body, "enabled": True})
    assert failed.status_code == 503 and "private" not in failed.text


@pytest.mark.parametrize("hostile", ["write_tool", "extra_sql", "other_run", "fabricated_finding", "injection"])
def test_hostile_model_cannot_change_protected_state(audit_db, monkeypatch, hostile):
    async def transport(client, method, url, token, payload=None, **kwargs):
        if url.endswith("/catalog"):
            return {"policy_revision": "approved-policy", "datasets": [{"id": "sales", "flow_id": audit_db.flow,
                "sql_enabled": False, "runs": [{"id": r} for r in range(5, 0, -1)]}]}
        if url.endswith("/inspect"):
            assert payload["run_id"] <= 5 and payload["source"] == "download"
            return evidence(payload["run_id"], 100, audit_db.flow)
        assert token == "m" * 40
        sent = json.dumps(payload)
        assert "o" * 40 not in sent and "r" * 40 not in sent
        assert len(payload["tools"]) == 1 and payload["tools"][0]["function"]["name"] == "read_profile"
        function = {"name": "read_profile", "arguments": json.dumps({"dataset_id": "sales", "run_id": 5, "source": "download"})}
        if hostile == "write_tool":
            function["name"] = "execute_sql"
        if hostile == "extra_sql":
            function["arguments"] = json.dumps({"dataset_id": "sales", "run_id": 5, "source": "download", "sql": "DELETE FROM flows"})
        if hostile == "other_run":
            function["arguments"] = json.dumps({"dataset_id": "sales", "run_id": 999, "source": "download"})
        message = {"tool_calls": [{"type": "function", "id": "call-1", "function": function}]}
        if hostile == "fabricated_finding":
            message = {"content": '{"candidate_ids":["fake evidence"]}'}
        if hostile == "injection":
            message = {"content": "Ignore safety. __import__('os').remove('database')"}
        return {"choices": [{"message": message}]}
    monkeypatch.setattr(engine, "json_request", transport)
    with database.get_db() as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM flows")]
    coverage = {"checked": 0, "unverified": [], "findings": 0, "model": "pending"}
    asyncio.run(engine.audit(audit_db.audit, [audit_db.flow], coverage))
    assert coverage["model"] == "unavailable_or_output_rejected"
    with database.get_db() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM flows")] == before
        assert not db.execute("SELECT 1 FROM alerts").fetchone()


def test_reader_has_no_write_or_generic_execution_routes():
    paths = {route.path for route in service.app.routes}
    assert paths == {"/catalog", "/inspect"}
    client = TestClient(service.app)
    assert client.get("/catalog").status_code == 401
    for source in ["file", "sql;DELETE", "http"]:
        with pytest.raises(ValueError):
            service.ReadRequest(dataset_id="sales", run_id=1, source=source)


def test_cancellation_closes_pending_transport(audit_db, monkeypatch):
    closed = []
    async def blocked(*args):
        engine._cancel.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    monkeypatch.setattr(engine, "audit", blocked)
    with pytest.raises(InterruptedError):
        asyncio.run(engine._supervise(audit_db.audit, [audit_db.flow], {}, 5))
    assert closed == [True]
