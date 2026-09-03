"""Fail-closed coverage for signed, non-executable Flow test commands."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from ecdsa import Ed25519, SigningKey
from fastapi import HTTPException

from app import database, main, remote_flow_control, settings
from app.routers import flows


INSTALLATION = "a" * 32
FLOW_NAME = "Exact  Flow_Name"


@pytest.fixture()
def control_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "remote-control.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(remote_flow_control, "_poll_lock", threading.Lock())
    monkeypatch.setattr(remote_flow_control, "_last_etag", None)
    database.init_db()
    settings.set_setting(remote_flow_control.INSTALLATION_SETTING, INSTALLATION)
    settings.set_setting(remote_flow_control.ENABLED_SETTING, "1")
    with database.get_db() as db:
        site_id = db.execute(
            "INSERT INTO flow_sites(name, adapter, enabled) VALUES ('Test', 'web_export', 1)"
        ).lastrowid
        report_id = db.execute(
            """INSERT INTO flow_reports(site_id, name, report_url, enabled)
               VALUES (?, 'Report', 'https://example.test/report', 1)""",
            (site_id,),
        ).lastrowid
        flow_id = db.execute(
            """INSERT INTO flows
               (name, source_type, site_id, report_id, enabled, target_folder,
                filename_template)
               VALUES (?, 'portal', ?, ?, 1, 'C:\\Exports', '{flow}.csv')""",
            (FLOW_NAME, site_id, report_id),
        ).lastrowid
    return flow_id


@pytest.fixture()
def signer(monkeypatch):
    key = SigningKey.generate(curve=Ed25519)
    monkeypatch.setattr(
        remote_flow_control,
        "PUBLIC_KEY_BASE64",
        base64.b64encode(key.verifying_key.to_string()).decode("ascii"),
    )
    return key


def _command(key, flow_id, *, name=FLOW_NAME, installation=INSTALLATION, now=None, **changes):
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    payload = {
        "version": 1,
        "command_id": str(uuid.uuid4()),
        "action": "run_flow",
        "installation_id": installation,
        "flow_id": flow_id,
        "flow_name_sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    payload.update(changes)
    payload["signature"] = base64.b64encode(
        key.sign(remote_flow_control.canonical_command_bytes(payload))
    ).decode("ascii")
    return payload


def _raw(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def test_control_defaults_enabled_but_local_emergency_off_persists(control_db):
    with database.get_db() as db:
        db.execute(
            "DELETE FROM app_settings WHERE key=?",
            (remote_flow_control.ENABLED_SETTING,),
        )

    assert remote_flow_control.is_enabled() is True

    remote_flow_control.set_enabled(False)

    assert remote_flow_control.is_enabled() is False
    assert settings.get_setting(remote_flow_control.ENABLED_SETTING) == "0"


def test_exact_signed_payload_round_trips_without_flow_name(control_db, signer):
    payload = _command(signer, control_db)
    raw = _raw(payload)

    verified = remote_flow_control.parse_and_verify_command(raw)

    assert verified["flow_id"] == control_db
    assert FLOW_NAME.encode() not in raw
    assert set(payload) == remote_flow_control._COMMAND_KEYS


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda payload: payload.update(action="stop_flow"), "unsupported_action"),
        (lambda payload: payload.update(extra=True), "invalid_schema"),
        (lambda payload: payload.update(flow_id=True), "invalid_flow_id"),
        (lambda payload: payload.update(installation_id="short"), "invalid_installation_id"),
    ],
)
def test_schema_rejections_happen_before_signature_use(control_db, signer, mutation, reason):
    payload = _command(signer, control_db)
    mutation(payload)
    with pytest.raises(remote_flow_control.CommandRejected, match=reason):
        remote_flow_control.parse_and_verify_command(_raw(payload))


def test_tampering_and_duplicate_json_keys_are_rejected(control_db, signer):
    payload = _command(signer, control_db)
    payload["flow_id"] += 1
    with pytest.raises(remote_flow_control.CommandRejected, match="invalid_signature"):
        remote_flow_control.parse_and_verify_command(_raw(payload))

    duplicate = _raw(_command(signer, control_db)).decode().replace(
        '"version":1', '"version":1,"version":1', 1
    )
    with pytest.raises(remote_flow_control.CommandRejected, match="duplicate_json_key"):
        remote_flow_control.parse_and_verify_command(duplicate.encode())


def test_clock_bounds_and_size_limit_are_fail_closed(control_db, signer):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expired = _command(
        signer,
        control_db,
        now=now - timedelta(minutes=20),
    )
    with pytest.raises(remote_flow_control.CommandRejected, match="expired"):
        remote_flow_control.parse_and_verify_command(_raw(expired), now=now)

    future = _command(signer, control_db, now=now + timedelta(minutes=3))
    with pytest.raises(remote_flow_control.CommandRejected, match="issued_in_future"):
        remote_flow_control.parse_and_verify_command(_raw(future), now=now)

    with pytest.raises(remote_flow_control.CommandRejected, match="invalid_size"):
        remote_flow_control.parse_and_verify_command(b"x" * (remote_flow_control.MAX_COMMAND_BYTES + 1))


def test_one_command_queues_once_and_creates_one_navigation(control_db, signer, monkeypatch):
    payload = _command(signer, control_db)
    calls = []

    def queue(flow_id, **kwargs):
        calls.append((flow_id, kwargs))
        return {"id": 41}

    monkeypatch.setattr(flows, "queue_flow_run", queue)
    fetcher = lambda: remote_flow_control.FetchResult(200, _raw(payload), '"etag-1"')

    first = remote_flow_control.poll_once(fetcher)
    second = remote_flow_control.poll_once(fetcher)

    assert first == {"status": "queued", "command_id": payload["command_id"], "run_id": 41}
    assert second == {"status": "rejected", "reason_code": "replay"}
    assert len(calls) == 1
    assert calls[0][0] == control_db
    assert calls[0][1] == {
        "actor": "signed-remote-test",
        "trigger_type": "remote_test",
        "allow_queued_resume": False,
        "require_enabled": True,
        "expected_name_sha256": payload["flow_name_sha256"],
    }
    assert remote_flow_control.pending_navigation() == {
        "command_id": payload["command_id"],
        "run_id": 41,
    }
    assert remote_flow_control.acknowledge_navigation(payload["command_id"]) is True
    assert remote_flow_control.pending_navigation() is None


def test_wrong_installation_name_change_and_disabled_flow_never_queue(
    control_db, signer, monkeypatch
):
    monkeypatch.setattr(
        flows,
        "queue_flow_run",
        lambda *_args, **_kwargs: pytest.fail("unsafe command reached the Flow queue"),
    )
    cases = [
        (_command(signer, control_db, installation="b" * 32), "wrong_installation"),
        (_command(signer, control_db, name=FLOW_NAME.lower()), "flow_identity_mismatch"),
    ]
    for payload, reason in cases:
        result = remote_flow_control.poll_once(
            lambda payload=payload: remote_flow_control.FetchResult(200, _raw(payload))
        )
        assert result == {"status": "rejected", "reason_code": reason}

    with database.get_db() as db:
        db.execute("UPDATE flows SET enabled=0 WHERE id=?", (control_db,))
    disabled = _command(signer, control_db)
    assert remote_flow_control.poll_once(
        lambda: remote_flow_control.FetchResult(200, _raw(disabled))
    ) == {"status": "rejected", "reason_code": "flow_disabled"}


def test_rate_limit_and_active_remote_run_block_new_commands(control_db, signer, monkeypatch):
    monkeypatch.setattr(
        flows,
        "queue_flow_run",
        lambda *_args, **_kwargs: pytest.fail("blocked command reached queue"),
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with database.get_db() as db:
        for index in range(3):
            db.execute(
                """INSERT INTO remote_flow_commands
                   (command_id, action, flow_id, run_id, status, received_at)
                   VALUES (?, 'run_flow', ?, ?, 'queued', ?)""",
                (str(uuid.uuid4()), control_db, index + 1, now),
            )
    command = _command(signer, control_db)
    result = remote_flow_control.poll_once(
        lambda: remote_flow_control.FetchResult(200, _raw(command))
    )
    assert result == {"status": "rejected", "reason_code": "rate_limited"}

    with database.get_db() as db:
        db.execute("DELETE FROM remote_flow_commands")
        db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json)
               VALUES (?, 'remote_test', 'running', '{}')""",
            (control_db,),
        )
    command = _command(signer, control_db)
    result = remote_flow_control.poll_once(
        lambda: remote_flow_control.FetchResult(200, _raw(command))
    )
    assert result == {"status": "rejected", "reason_code": "remote_run_active"}


def test_disabled_poll_does_not_fetch_and_emergency_off_clears_navigation(
    control_db, signer, monkeypatch
):
    payload = _command(signer, control_db)
    monkeypatch.setattr(flows, "queue_flow_run", lambda *_args, **_kwargs: {"id": 55})
    remote_flow_control.poll_once(
        lambda: remote_flow_control.FetchResult(200, _raw(payload))
    )
    remote_flow_control.set_enabled(False)

    assert remote_flow_control.poll_once(
        lambda: pytest.fail("disabled control contacted GitHub")
    ) == {"status": "disabled"}
    assert remote_flow_control.pending_navigation() is None


def test_queue_helper_rechecks_name_and_enabled_in_creation_transaction(control_db):
    with pytest.raises(HTTPException, match="identity changed"):
        flows.queue_flow_run(
            control_db,
            actor="signed-remote-test",
            trigger_type="remote_test",
            allow_queued_resume=False,
            require_enabled=True,
            expected_name_sha256="0" * 64,
        )
    with database.get_db() as db:
        db.execute("UPDATE flows SET enabled=0 WHERE id=?", (control_db,))
    with pytest.raises(HTTPException, match="disabled"):
        flows.queue_flow_run(
            control_db,
            actor="signed-remote-test",
            trigger_type="remote_test",
            allow_queued_resume=False,
            require_enabled=True,
            expected_name_sha256=hashlib.sha256(FLOW_NAME.encode()).hexdigest(),
        )


def test_control_endpoints_use_socket_locality_not_permissive_access(control_db, monkeypatch):
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    remote = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"))
    monkeypatch.setattr(main, "_is_localhost", lambda ip: ip == "127.0.0.1")

    assert main.get_remote_flow_control(local)["installation_id"] == INSTALLATION
    with pytest.raises(HTTPException) as rejected:
        main.get_remote_flow_control(remote)
    assert rejected.value.status_code == 403


def test_frontend_only_navigates_to_integer_run_and_shows_local_controls():
    root = Path(__file__).parents[1]
    listener = (root / "app" / "static" / "remote_flow_control.js").read_text()
    app_source = (root / "app" / "static" / "app.js").read_text()
    index = (root / "app" / "static" / "index.html").read_text()
    log_page = (root / "app" / "static" / "flow_run_log.html").read_text()

    assert "Number.isSafeInteger(runId)" in listener
    assert "location.assign(destination)" in listener
    assert "intent.path" not in listener
    assert "Emergency off" in app_source
    assert "Remote target:" in app_source
    assert "remote_flow_control.js?v=1" in index
    assert "remote_flow_control.js?v=1" in log_page
