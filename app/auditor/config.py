from dataclasses import dataclass
from functools import lru_cache
import os
import json
from urllib.parse import urlsplit


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
    operator_token: str
    manifest_path: str
    flow_ids: tuple[int, ...]


@lru_cache(maxsize=1)
def settings():
    # Deliberately independent of editable AI settings and uploader variables.
    reader_token = os.environ.get("METRONOME_AUDIT_READER_TOKEN", "")
    operator_token = os.environ.get("METRONOME_AUDIT_OPERATOR_TOKEN", "")
    model_token = os.environ.get("METRONOME_AUDIT_MODEL_TOKEN", "")
    if min(len(reader_token), len(operator_token)) < 32 or reader_token == operator_token or model_token in {reader_token, operator_token}:
        raise ValueError("Distinct auditor access keys must be configured.")
    try:
        flows = json.loads(os.environ.get("METRONOME_AUDIT_FLOW_IDS", "[]"))
    except ValueError:
        raise ValueError("Approved auditor flows are not configured.") from None
    manifest_path = os.environ.get("METRONOME_AUDIT_MANIFEST_PATH", "")
    if not manifest_path or not isinstance(flows, list) or not 1 <= len(flows) <= 100 or any(type(i) is not int or i <= 0 for i in flows):
        raise ValueError("The separate audit manifest and approved flows must be configured.")
    return Config(
        endpoint(os.environ.get("METRONOME_AUDIT_READER_URL", "")), reader_token,
        endpoint(os.environ.get("METRONOME_AUDIT_MODEL_URL", "")), model_token,
        os.environ.get("METRONOME_AUDIT_MODEL", "Qwen/Qwen3.8-27B"), operator_token,
        manifest_path, tuple(sorted(set(flows))))


def readiness():
    try:
        settings()
        return {"configured": True, "detail": "Auditor connections are configured. Access is verified when a run starts."}
    except ValueError:
        return {"configured": False, "detail": "An administrator must configure the restricted reader, model endpoint, and operator access key."}
