"""Managed configuration, exact-path scope, and installer boundary contracts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.auditor import config
from auditor_reader import policy, profiles
from tools.provision_auditor import provision
from test_data_auditor import tmp_path  # Stable non-symlink Windows fixture.

ROOT = Path(__file__).resolve().parents[1]


def test_provision_preserves_generated_secrets_and_separates_host_reader(tmp_path):
    host_path, reader_path = provision(tmp_path / "auditor", "http://127.0.0.1:8766",
                                       "postgresql://probe:fixture@127.0.0.1:5432/fixture")
    host = json.loads(host_path.read_text(encoding="utf-8"))
    reader = json.loads(reader_path.read_text(encoding="utf-8"))
    assert host["reader_token"] == reader["reader_token"]
    assert len(host["reader_token"]) >= 32 and len(reader["category_key"]) >= 32
    assert "category_key" not in host and "reader_dsn" not in host
    assert "postgresql://" not in host_path.read_text(encoding="utf-8")
    original = host["reader_token"], reader["category_key"], reader["reader_dsn"]
    provision(tmp_path / "auditor", "http://127.0.0.1:8766")
    host = json.loads(host_path.read_text(encoding="utf-8"))
    reader = json.loads(reader_path.read_text(encoding="utf-8"))
    assert (host["reader_token"], reader["category_key"], reader["reader_dsn"]) == original


def test_provision_cli_reuses_probe_identity_but_never_uploader_credentials(tmp_path):
    environment = {**os.environ, "PGHOST":"127.0.0.1", "PGPORT":"5432",
                   "PGDATABASE":"fixture", "PGUSER":"probe", "PGPASSWORD":"probe-secret",
                   "DG_UPLOAD_PGUSER":"uploader", "DG_UPLOAD_PGPASSWORD":"upload-secret"}
    result = subprocess.run([sys.executable, str(ROOT / "tools/provision_auditor.py"),
                             "--root", str(tmp_path / "managed")], env=environment,
                            text=True, capture_output=True, check=True)
    reader = json.loads((tmp_path / "managed/reader/reader.json").read_text(encoding="utf-8"))
    assert "probe-secret" in reader["reader_dsn"]
    assert "uploader" not in json.dumps(reader) and "upload-secret" not in json.dumps(reader)
    assert "probe-secret" not in result.stdout


def test_exact_registered_path_rejects_another_file_in_same_folder(tmp_path):
    approved_file = tmp_path / "approved.csv"
    other_file = tmp_path / "other.csv"
    approved_file.write_text("Name\nApproved\n", encoding="utf-8")
    other_file.write_text("Name\nOther\n", encoding="utf-8")
    dataset = policy.Dataset(id="flow_1", flow_id=1, artifact_paths=(str(approved_file),),
                             columns=(policy.Column(name="name", csv_header="Name"),))
    approved = policy.Policy(manifest_db=str(tmp_path / "manifest.sqlite"), datasets=(dataset,))
    artifact = {"file_path":str(other_file), "status":"saved",
                "checksum":__import__("hashlib").sha256(other_file.read_bytes()).hexdigest()}
    with pytest.raises(policy.InspectionError, match="artifact_not_registered"):
        profiles.profile_csv([artifact], dataset, approved, b"s" * 32, lambda: False)


def test_setup_installs_loopback_virtual_reader_and_denies_app_database():
    source = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    assert "MetronomeAuditorReader" in source
    assert "--host 127.0.0.1 --port $AuditorPort --no-access-log" in source
    assert '"NT SERVICE\\$AuditorServiceName"' in source
    assert "icacls.exe $AuditorDeniedDatabaseFile /deny" in source
    assert "DG_AUDITOR_READER_CONFIG=$AuditorReaderConfig" in source
    assert "DG_AUDITOR_HOST_CONFIG=$AuditorHostConfig" in source
    assert "DG_UPLOAD_PGUSER" not in (ROOT / "tools/provision_auditor.py").read_text(encoding="utf-8")


def test_public_readiness_never_contains_managed_credentials(tmp_path, monkeypatch):
    host_path, _ = provision(tmp_path / "auditor", "http://127.0.0.1:8766")
    monkeypatch.setenv("DG_AUDITOR_HOST_CONFIG", str(host_path))
    monkeypatch.setattr(config, "load_runtime_settings", lambda: type("AI", (), {
        "qwen_enabled":True, "endpoint":"http://127.0.0.1:9000/v1/chat/completions",
        "model":"synthetic", "api_key":"model-secret"})())
    private = json.loads(host_path.read_text(encoding="utf-8"))["reader_token"]
    assert private not in json.dumps(config.readiness())
    assert "model-secret" not in json.dumps(config.readiness())
