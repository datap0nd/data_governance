"""Synthetic manifest and reader integration; no application secrets exposed."""
import hashlib
import json

from fastapi.testclient import TestClient

from app import database
from app.auditor import config, manifest
from auditor_reader import policy, service
from test_data_auditor import audit_db, tmp_path  # Shared isolated fixture, no live state.


def test_manifest_is_separate_sanitized_and_reader_recounts(audit_db, monkeypatch):
    path = audit_db.root / "completed.csv"
    path.write_text("Region,Units\nNorth,10\nSouth,20\n", encoding="utf-8")
    job = {"report": {"url": "https://private.invalid/secret-report"},
           "selections": {"private": "business selection"},
           "downloads": {"periods": [["2026-W30"]]},
           "password": "secret-must-not-leak", "transformation": {"enabled": False}}
    artifacts = [{"file_path": str(path), "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "status": "saved", "row_count": 99999, "period_key": ["2026-W30"], "script_stdout": "private-output"}]
    with database.get_db() as db:
        run = db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,artifact_json,finished_at) VALUES(?,'manual','succeeded',?,?,?)",
                         (audit_db.flow, json.dumps(job), json.dumps(artifacts), "2026-08-10T10:00:00+00:00")).lastrowid
    cfg = config.settings()
    manifest.publish(cfg)
    data = open(cfg.manifest_path, "rb").read()
    for secret in [b"secret-must-not-leak", b"secret-report", b"business selection", b"private-output"]:
        assert secret not in data
    approved = {"manifest_db": cfg.manifest_path, "artifact_roots": [str(audit_db.root)], "datasets": [
        {"id": "sales", "flow_id": audit_db.flow, "columns": [{"name": "region", "csv_header": "Region"}, {"name": "units", "csv_header": "Units", "kind": "number"}]}]}
    monkeypatch.setattr(service, "load_policy", lambda: policy.Policy.model_validate_json(json.dumps(approved)))
    monkeypatch.setenv("METRONOME_AUDIT_READER_TOKEN", "r" * 40)
    monkeypatch.setenv("METRONOME_AUDIT_CATEGORY_KEY", "s" * 40)
    client = TestClient(service.app, client=("127.0.0.1", 50100))
    headers = {"Authorization": "Bearer " + "r" * 40}
    catalog = client.get("/catalog", headers=headers).json()
    assert catalog["datasets"][0]["runs"][0]["id"] == run
    body = {"dataset_id": "sales", "run_id": run, "source": "download"}
    result = client.post("/inspect", headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["profile"]["rows"] == 2
    assert result.json()["profile"]["columns"]["units"]["sum"] == "30"
    assert not result.json()["sql_comparable"]
    assert client.post("/inspect", headers=headers, json={**body, "file_path": "C:/sensitive"}).status_code == 422
    assert client.post("/inspect", headers=headers, json={**body, "dataset_id": "unapproved"}).status_code == 422
    assert client.post("/inspect", headers=headers, json={**body, "run_id": run + 1}).status_code == 422


def test_main_database_cannot_be_used_as_manifest(audit_db):
    from dataclasses import replace
    import pytest
    cfg = replace(config.settings(), manifest_path=database.DB_PATH)
    with database.get_db() as db:
        before = db.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
    with pytest.raises(ValueError, match="unrelated database"):
        manifest.publish(cfg)
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == before


def test_completed_windows_keep_filters_and_reject_partial_periods():
    from test_data_auditor import dataset
    approved = dataset().model_copy(update={"period_comparison": "completed_windows"})
    def scope(week, finished="2026-09-15T10:00:00+00:00", selections=None):
        return service.reporting_scope({"downloads": {"periods": [[week]], "period_strategy": "rolling"},
            "selections": selections or {"region": "same"}}, [], approved, finished)
    assert scope("2026-W30") == scope("2026-W31")
    assert scope("2026-W30") != scope("2026-W38")  # Still in progress on Sept 15.
    assert scope("2026-W30") != scope("2026-W30", selections={"region": "changed"})
