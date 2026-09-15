"""Private managed configuration for the automatic Flow auditor.

The reader credential is deliberately stored outside SQLite and is never part
of an HTTP projection. The model connection is the already configured local
AI provider; each audit snapshots it before work starts.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from app import database
from app.ai.runtime_config import load_runtime_settings


def endpoint(value, suffix=""):
    parsed = urlsplit(value)
    if not value or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Auditor endpoint configuration is invalid.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Remote auditor endpoints require HTTPS.")
    return value.rstrip("/") + suffix


@dataclass(frozen=True)
class Config:
    reader_url: str
    reader_token: str
    model_url: str
    model_token: str
    model: str
    manifest_path: str
    policy_path: str = ""


def host_config_path() -> Path:
    configured = os.environ.get("DG_AUDITOR_HOST_CONFIG", "").strip()
    if configured:
        return Path(configured)
    return Path(database.DB_PATH).resolve().parent / "auditor" / "host" / "host.json"


def _managed_reader():
    path = host_config_path()
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("version") != 1:
            raise ValueError("The managed auditor configuration needs an update.")
        return value
    # Compatibility for the first manually provisioned release. Current
    # validation still applies and selected-Flow/operator variables are ignored.
    token = os.environ.get("METRONOME_AUDIT_READER_TOKEN", "")
    manifest_path = os.environ.get("METRONOME_AUDIT_MANIFEST_PATH", "")
    policy_path = os.environ.get("METRONOME_AUDIT_READER_POLICY", "")
    reader_url = os.environ.get("METRONOME_AUDIT_READER_URL", "")
    if reader_url and manifest_path and policy_path and len(token) >= 32:
        return {"version": 1, "reader_url": reader_url,
                "reader_token": token, "manifest_path": manifest_path,
                "policy_path": policy_path}
    raise ValueError("The managed restricted reader is not installed.")


def settings():
    reader = _managed_reader()
    reader_token = str(reader.get("reader_token") or "")
    manifest_path = Path(str(reader.get("manifest_path") or ""))
    policy_path = Path(str(reader.get("policy_path") or ""))
    if len(reader_token) < 32 or not manifest_path.is_absolute() or not policy_path.is_absolute():
        raise ValueError("The managed restricted reader configuration is invalid.")
    ai = load_runtime_settings()
    model_url = endpoint(ai.endpoint) if ai.qwen_enabled and ai.endpoint and ai.model else ""
    return Config(endpoint(str(reader.get("reader_url") or "")), reader_token,
                  model_url, ai.api_key if model_url else "", ai.model if model_url else "",
                  str(manifest_path), str(policy_path))


def readiness():
    reader = {"available": False, "detail": "The managed restricted reader is not installed."}
    model = {"available": False, "detail": "Configure Local AI in System → AI."}
    try:
        value = _managed_reader()
        endpoint(str(value.get("reader_url") or ""))
        if len(str(value.get("reader_token") or "")) < 32:
            raise ValueError()
        reader = {"available": True, "detail": "Managed reader configured; access is verified during each audit."}
    except Exception:
        pass
    try:
        ai = load_runtime_settings()
        if ai.qwen_enabled and ai.endpoint and ai.model:
            endpoint(ai.endpoint)
            model = {"available": True, "detail": f"Local AI ready: {ai.model}."}
    except Exception:
        pass
    return {"configured": reader["available"],
            "reader": reader, "model": model}
