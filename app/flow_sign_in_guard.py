"""Stops a portal sign-in from being submitted again after it has failed.

Every Flow browser profile signs in to ASAP and GSCM with the one portal
sign-in. Each profile used to submit it on its own, for each portal, and
nothing remembered a refusal: after a password change one setup run, or one
round of expired sessions, was a row of wrong attempts against the person's
own company account, enough to lock it.

A small record beside the browser profiles is shared by every worker of the
Windows account:

* one process submits at a time, and each reads the record first;
* a sign-in the portal refused is not submitted again;
* a sign-in that was submitted and got nowhere twice in a row is not
  submitted again either: the cause may be the password, or a prompt nobody
  answered, and a third try will not tell them apart;
* a sign-in that works clears the count;
* a different user ID or password starts clean, and a person can say "try
  it again" for the same one on the Settings page.

The record never holds the password. It holds a salted fingerprint of the
user ID and password, which is only good for noticing that they changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    from app import flow_credentials
except ModuleNotFoundError:  # flow_worker.py run as a plain file
    import flow_credentials

STATE_FILENAME = ".portal_sign_in_state.json"
LOCK_FILENAME = ".portal_sign_in.lock"

#: Submissions of one sign-in that may end without the portal opening.
MAX_FAILURES = 2
#: How long a process waits for another one that is signing in right now.
LOCK_WAIT_SECONDS = 900

WHERE_TO_FIX = "Replace it under System > Settings > Connections > Portal sign-in"


class SignInPaused(RuntimeError):
    """The portal sign-in must not be submitted; nothing was sent."""


def _directory(profile_dir: Path | None = None) -> Path:
    return Path(profile_dir) if profile_dir is not None else flow_credentials.flow_profile_dir()


def _read(directory: Path) -> dict:
    try:
        state = json.loads((directory / STATE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write(directory: Path, state: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STATE_FILENAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def _fingerprint(credential: dict, salt: str) -> str:
    material = f"{salt}\0{credential.get('username', '')}\0{credential.get('password', '')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_for(credential: dict, state: dict) -> dict:
    """The record of this exact sign-in; any other one starts clean."""
    salt = state.get("salt") or secrets.token_hex(16)
    fingerprint = _fingerprint(credential, salt)
    if state.get("fingerprint") == fingerprint:
        return state
    return {"salt": salt, "fingerprint": fingerprint, "failures": []}


def _paused_message(record: dict) -> str:
    portal = record.get("paused_portal") or "the portal"
    if record.get("paused_refused"):
        reason = f"{portal} refused the saved portal sign-in"
    else:
        reason = f"The saved portal sign-in was submitted {len(record.get('failures') or [])} times without {portal} opening"
    return (
        f"{reason}, so Metronome has stopped using it: repeating a wrong password would lock the account. "
        f"{WHERE_TO_FIX}, or choose Try again there if the password is right."
    )


@contextmanager
def _lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / LOCK_FILENAME).open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SignInPaused(
                        "Another Metronome worker has been signing in to the portal for "
                        f"{LOCK_WAIT_SECONDS // 60} minutes. This one did not submit the portal sign-in."
                    ) from None
                time.sleep(0.5)
        try:
            yield
        finally:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        handle.close()


class _Attempt:
    """What became of one submission. Unsaid, a submitted sign-in counts as failed."""

    def __init__(self):
        self.was_submitted = False
        self.result = None
        self.was_refused = False

    def submitted(self):
        self.was_submitted = True

    def succeeded(self):
        self.result = "succeeded"

    def refused(self):
        self.result, self.was_refused = "failed", True

    def failed(self):
        self.result = "failed"


@contextmanager
def attempt(credential: dict, portal: str, profile_dir: Path | None = None):
    """Submit the sign-in inside this block, or get ``SignInPaused`` before anything is sent.

    Call ``submitted()`` once the form has been sent, then ``succeeded()``,
    ``refused()`` (the portal said the user ID or password is wrong) or
    ``failed()``. Leaving the block after ``submitted()`` without an outcome
    counts as a failure, whatever the reason.
    """
    directory = _directory(profile_dir)
    with _lock(directory):
        record = _record_for(credential, _read(directory))
        if record.get("paused"):
            raise SignInPaused(_paused_message(record))
        outcome = _Attempt()
        try:
            yield outcome
        finally:
            if outcome.result == "succeeded":
                _write(directory, {"salt": record["salt"], "fingerprint": record["fingerprint"], "failures": []})
            elif outcome.result == "failed" or outcome.was_submitted:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                failures = [*(record.get("failures") or []),
                            {"at": now, "portal": portal, "refused": outcome.was_refused}]
                paused = outcome.was_refused or len(failures) >= MAX_FAILURES
                _write(directory, {
                    "salt": record["salt"], "fingerprint": record["fingerprint"], "failures": failures,
                    "paused": paused, "paused_at": now if paused else None,
                    "paused_portal": portal if paused else None, "paused_refused": outcome.was_refused,
                })


def raise_if_paused(credential: dict, profile_dir: Path | None = None) -> None:
    """Refuse early, before a browser is even opened, when the sign-in is paused."""
    record = _record_for(credential, _read(_directory(profile_dir)))
    if record.get("paused"):
        raise SignInPaused(_paused_message(record))


def status(credential: dict | None, profile_dir: Path | None = None) -> dict:
    """Whether the current sign-in is paused, for the Settings page. No secret in it."""
    clear = {"paused": False, "paused_at": None, "paused_portal": None, "paused_refused": False, "failed_attempts": 0}
    if not credential:
        return clear
    record = _record_for(credential, _read(_directory(profile_dir)))
    return {
        "paused": bool(record.get("paused")),
        "paused_at": record.get("paused_at"),
        "paused_portal": record.get("paused_portal"),
        "paused_refused": bool(record.get("paused_refused")),
        "failed_attempts": len(record.get("failures") or []),
    }


def resume(profile_dir: Path | None = None) -> None:
    """A person says the sign-in is right after all: forget the failures."""
    directory = _directory(profile_dir)
    with _lock(directory):
        state = _read(directory)
        if state:
            _write(directory, {"salt": state.get("salt") or secrets.token_hex(16),
                               "fingerprint": state.get("fingerprint"), "failures": []})
