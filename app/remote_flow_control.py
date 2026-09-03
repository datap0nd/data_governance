"""Signed, non-executable GitHub transport for bounded Flow test runs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from ecdsa import BadSignatureError, Ed25519, VerifyingKey
from fastapi import HTTPException

from app.database import get_db
from app.scanner.pbi_auth import resolve_proxy
from app.settings import get_setting, set_setting


logger = logging.getLogger(__name__)

COMMAND_URL = (
    "https://raw.githubusercontent.com/datap0nd/data_governance/"
    "metronome-control/.metronome-control/command.json"
)
PUBLIC_KEY_BASE64 = "ObaQLklTZN5Jyv6wOO/tR2WmROfhTVFBpYNzPmKlyYU="
MAX_COMMAND_BYTES = 8 * 1024
MAX_COMMAND_AGE_SECONDS = 10 * 60
MAX_FUTURE_SKEW_SECONDS = 2 * 60
RATE_LIMIT_PER_HOUR = 3
NAVIGATION_TTL_SECONDS = 15 * 60

ENABLED_SETTING = "remote_flow_control_enabled"
INSTALLATION_SETTING = "remote_flow_control_installation_id"
STATE_SETTING = "remote_flow_control_state_v1"

_COMMAND_KEYS = frozenset(
    {
        "version",
        "command_id",
        "action",
        "installation_id",
        "flow_id",
        "flow_name_sha256",
        "issued_at",
        "expires_at",
        "signature",
    }
)
_INSTALLATION_RE = re.compile(r"^[a-f0-9]{32}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_poll_lock = threading.Lock()
_etag_lock = threading.Lock()
_last_etag: str | None = None


class CommandRejected(ValueError):
    """A safe, non-sensitive reason that a command could not be accepted."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    body: bytes = b""
    etag: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise CommandRejected(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommandRejected(code) from exc
    if parsed.tzinfo is None:
        raise CommandRejected(code)
    return parsed.astimezone(timezone.utc)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CommandRejected("duplicate_json_key")
        result[key] = value
    return result


def canonical_command_bytes(payload: dict) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_and_verify_command(raw: bytes, *, now: datetime | None = None) -> dict:
    """Strictly parse and authenticate one complete command document."""
    if not raw or len(raw) > MAX_COMMAND_BYTES:
        raise CommandRejected("invalid_size")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except CommandRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandRejected("invalid_json") from exc
    if not isinstance(payload, dict) or set(payload) != _COMMAND_KEYS:
        raise CommandRejected("invalid_schema")
    if isinstance(payload["version"], bool) or payload["version"] != 1:
        raise CommandRejected("unsupported_version")
    if payload["action"] != "run_flow":
        raise CommandRejected("unsupported_action")
    try:
        command_id = str(uuid.UUID(payload["command_id"]))
    except (AttributeError, TypeError, ValueError) as exc:
        raise CommandRejected("invalid_command_id") from exc
    if command_id != payload["command_id"]:
        raise CommandRejected("invalid_command_id")
    if not isinstance(payload["installation_id"], str) or not _INSTALLATION_RE.fullmatch(
        payload["installation_id"]
    ):
        raise CommandRejected("invalid_installation_id")
    if (
        isinstance(payload["flow_id"], bool)
        or not isinstance(payload["flow_id"], int)
        or payload["flow_id"] <= 0
    ):
        raise CommandRejected("invalid_flow_id")
    if not isinstance(payload["flow_name_sha256"], str) or not _SHA256_RE.fullmatch(
        payload["flow_name_sha256"]
    ):
        raise CommandRejected("invalid_flow_identity")

    issued_at = _parse_timestamp(payload["issued_at"], "invalid_issued_at")
    expires_at = _parse_timestamp(payload["expires_at"], "invalid_expires_at")
    if expires_at <= issued_at or (expires_at - issued_at).total_seconds() > MAX_COMMAND_AGE_SECONDS:
        raise CommandRejected("invalid_expiry_window")
    observed_at = (now or _utc_now()).astimezone(timezone.utc)
    if issued_at > observed_at + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise CommandRejected("issued_in_future")
    if expires_at < observed_at:
        raise CommandRejected("expired")

    try:
        signature = base64.b64decode(payload["signature"], validate=True)
        public_key = base64.b64decode(PUBLIC_KEY_BASE64, validate=True)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise CommandRejected("invalid_signature") from exc
    if len(signature) != 64 or len(public_key) != 32:
        raise CommandRejected("invalid_signature")
    try:
        VerifyingKey.from_string(public_key, curve=Ed25519).verify(
            signature, canonical_command_bytes(payload)
        )
    except (BadSignatureError, ValueError) as exc:
        raise CommandRejected("invalid_signature") from exc
    return {**payload, "issued_at_dt": issued_at, "expires_at_dt": expires_at}


def installation_id() -> str:
    current = get_setting(INSTALLATION_SETTING)
    if current and _INSTALLATION_RE.fullmatch(current):
        return current
    generated = secrets.token_hex(16)
    set_setting(INSTALLATION_SETTING, generated)
    return generated


def is_enabled() -> bool:
    # Signed Flow control is available immediately after deployment.  An
    # explicit local emergency-off remains authoritative and persists as "0".
    return get_setting(ENABLED_SETTING, "1") == "1"


def _safe_state(status: str, reason_code: str | None = None) -> dict:
    state = {
        "status": status,
        "reason_code": reason_code,
        "checked_at": _iso(_utc_now()),
    }
    set_setting(STATE_SETTING, json.dumps(state, sort_keys=True, separators=(",", ":")))
    return state


def set_enabled(enabled: bool) -> dict:
    set_setting(ENABLED_SETTING, "1" if enabled else "0")
    if not enabled:
        now = _iso(_utc_now())
        with get_db() as db:
            db.execute(
                """UPDATE remote_flow_commands SET navigation_ack_at=?
                   WHERE navigation_ack_at IS NULL AND run_id IS NOT NULL""",
                (now,),
            )
        _safe_state("disabled")
    else:
        _safe_state("enabled")
    return status_payload()


def _load_state() -> dict:
    raw = get_setting(STATE_SETTING, "{}") or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _accepted_since(db, cutoff: str) -> int:
    return int(
        db.execute(
            """SELECT COUNT(*) FROM remote_flow_commands
               WHERE run_id IS NOT NULL AND received_at >= ?""",
            (cutoff,),
        ).fetchone()[0]
    )


def status_payload() -> dict:
    now = _utc_now()
    cutoff = _iso(now - timedelta(hours=1))
    with get_db() as db:
        accepted = _accepted_since(db, cutoff)
        latest = db.execute(
            """SELECT command_id, action, flow_id, run_id, status, reason_code,
                      issued_at, received_at, completed_at
               FROM remote_flow_commands ORDER BY received_at DESC LIMIT 1"""
        ).fetchone()
        active = db.execute(
            """SELECT id, flow_id, status FROM flow_runs
               WHERE trigger_type='remote_test'
                 AND status IN ('queued','claimed','running')
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    return {
        "enabled": is_enabled(),
        "installation_id": installation_id(),
        "poll_interval_seconds": 30,
        "rate_limit_per_hour": RATE_LIMIT_PER_HOUR,
        "remaining_runs": max(0, RATE_LIMIT_PER_HOUR - accepted),
        "last_poll": _load_state(),
        "last_command": dict(latest) if latest else None,
        "active_remote_run": dict(active) if active else None,
        "allowed_actions": ["run_flow"],
    }


def _fetch_command() -> FetchResult:
    global _last_etag
    headers = {
        "Accept": "application/json,text/plain;q=0.9",
        "Cache-Control": "no-cache",
        "User-Agent": "Metronome-Signed-Flow-Control/1",
    }
    with _etag_lock:
        if _last_etag:
            headers["If-None-Match"] = _last_etag
    with httpx.Client(
        timeout=httpx.Timeout(10.0),
        proxy=resolve_proxy(COMMAND_URL),
        follow_redirects=False,
    ) as client:
        with client.stream("GET", COMMAND_URL, headers=headers) as response:
            if response.status_code in {304, 404}:
                return FetchResult(response.status_code, etag=response.headers.get("etag"))
            if response.status_code != 200:
                return FetchResult(response.status_code)
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "text/plain", "application/octet-stream"}:
                raise CommandRejected("invalid_content_type")
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_COMMAND_BYTES:
                        raise CommandRejected("invalid_size")
                except ValueError as exc:
                    raise CommandRejected("invalid_content_length") from exc
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_COMMAND_BYTES:
                    raise CommandRejected("invalid_size")
                chunks.append(chunk)
            return FetchResult(200, b"".join(chunks), response.headers.get("etag"))


def _reserve_command(command: dict) -> None:
    now = _utc_now()
    now_iso = _iso(now)
    cutoff = _iso(now - timedelta(hours=1))
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM remote_flow_commands WHERE command_id=?",
            (command["command_id"],),
        ).fetchone():
            raise CommandRejected("replay")
        if _accepted_since(db, cutoff) >= RATE_LIMIT_PER_HOUR:
            raise CommandRejected("rate_limited")
        if db.execute(
            """SELECT 1 FROM flow_runs WHERE trigger_type='remote_test'
               AND status IN ('queued','claimed','running') LIMIT 1"""
        ).fetchone():
            raise CommandRejected("remote_run_active")
        flow = db.execute(
            "SELECT id, name, enabled FROM flows WHERE id=?",
            (command["flow_id"],),
        ).fetchone()
        if not flow:
            raise CommandRejected("flow_not_found")
        if not bool(flow["enabled"]):
            raise CommandRejected("flow_disabled")
        observed_digest = hashlib.sha256(flow["name"].encode("utf-8")).hexdigest()
        if not secrets.compare_digest(observed_digest, command["flow_name_sha256"]):
            raise CommandRejected("flow_identity_mismatch")
        db.execute(
            """INSERT INTO remote_flow_commands
               (command_id, action, flow_id, status, issued_at, received_at)
               VALUES (?, 'run_flow', ?, 'processing', ?, ?)""",
            (command["command_id"], command["flow_id"], command["issued_at"], now_iso),
        )


def _finish_command(
    command_id: str,
    *,
    status: str,
    reason_code: str | None = None,
    run_id: int | None = None,
) -> None:
    now = _utc_now()
    navigation_expires = (
        _iso(now + timedelta(seconds=NAVIGATION_TTL_SECONDS)) if run_id else None
    )
    with get_db() as db:
        db.execute(
            """UPDATE remote_flow_commands
               SET status=?, reason_code=?, run_id=?, completed_at=?,
                   navigation_expires_at=?
               WHERE command_id=?""",
            (status, reason_code, run_id, _iso(now), navigation_expires, command_id),
        )


def _record_rejected_command(command: dict, reason_code: str) -> None:
    now = _iso(_utc_now())
    with get_db() as db:
        db.execute(
            """INSERT OR IGNORE INTO remote_flow_commands
               (command_id, action, flow_id, status, reason_code, issued_at,
                received_at, completed_at)
               VALUES (?, 'run_flow', ?, 'rejected', ?, ?, ?, ?)""",
            (
                command["command_id"],
                command["flow_id"],
                reason_code,
                command["issued_at"],
                now,
                now,
            ),
        )


def _process_command(command: dict) -> dict:
    if command["installation_id"] != installation_id():
        raise CommandRejected("wrong_installation")
    try:
        _reserve_command(command)
    except CommandRejected as exc:
        if exc.code != "replay":
            _record_rejected_command(command, exc.code)
        raise
    try:
        from app.routers.flows import queue_flow_run

        result = queue_flow_run(
            command["flow_id"],
            actor="signed-remote-test",
            trigger_type="remote_test",
            allow_queued_resume=False,
            require_enabled=True,
            expected_name_sha256=command["flow_name_sha256"],
        )
    except HTTPException as exc:
        code = "flow_queue_conflict" if exc.status_code in {409, 503} else "flow_queue_rejected"
        _finish_command(command["command_id"], status="rejected", reason_code=code)
        raise CommandRejected(code) from exc
    except Exception as exc:
        _finish_command(command["command_id"], status="rejected", reason_code="internal_error")
        raise CommandRejected("internal_error") from exc
    run_id = int(result["id"])
    _finish_command(command["command_id"], status="queued", run_id=run_id)
    return {"status": "queued", "command_id": command["command_id"], "run_id": run_id}


def poll_once(fetcher: Callable[[], FetchResult] | None = None) -> dict:
    """Poll and process at most one signed command; never raise to APScheduler."""
    global _last_etag
    if not is_enabled():
        return {"status": "disabled"}
    if not _poll_lock.acquire(blocking=False):
        return {"status": "poll_active"}
    try:
        try:
            fetched = (fetcher or _fetch_command)()
            if fetched.status_code == 304:
                return _safe_state("unchanged")
            if fetched.status_code == 404:
                return _safe_state("no_command")
            if fetched.status_code != 200:
                return _safe_state("fetch_failed", "http_status")
            command = parse_and_verify_command(fetched.body)
            result = _process_command(command)
            if fetched.etag:
                with _etag_lock:
                    _last_etag = fetched.etag
            _safe_state("command_queued")
            return result
        except CommandRejected as exc:
            _safe_state("command_rejected", exc.code)
            return {"status": "rejected", "reason_code": exc.code}
        except (httpx.HTTPError, OSError):
            logger.warning("Signed Flow control poll failed", exc_info=True)
            return _safe_state("fetch_failed", "network_error")
        except Exception:
            logger.exception("Signed Flow control poll failed safely")
            return _safe_state("poll_failed", "internal_error")
    finally:
        _poll_lock.release()


def pending_navigation() -> dict | None:
    if not is_enabled():
        return None
    now = _iso(_utc_now())
    with get_db() as db:
        row = db.execute(
            """SELECT command_id, run_id FROM remote_flow_commands
               WHERE run_id IS NOT NULL AND navigation_ack_at IS NULL
                 AND navigation_expires_at >= ?
               ORDER BY received_at DESC LIMIT 1""",
            (now,),
        ).fetchone()
    return dict(row) if row else None


def acknowledge_navigation(command_id: str) -> bool:
    try:
        normalized = str(uuid.UUID(command_id))
    except (TypeError, ValueError):
        return False
    if normalized != command_id:
        return False
    now = _iso(_utc_now())
    with get_db() as db:
        updated = db.execute(
            """UPDATE remote_flow_commands SET navigation_ack_at=?
               WHERE command_id=? AND run_id IS NOT NULL
                 AND navigation_ack_at IS NULL AND navigation_expires_at >= ?""",
            (now, command_id, now),
        ).rowcount
    return bool(updated)
