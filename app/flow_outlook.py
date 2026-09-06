"""Interactive Outlook attachment acquisition for Flow workers.

The worker normally runs in Windows session 0. Outlook automation must run in
the signed-in user's interactive session, so this module launches a short-lived
per-run Scheduled Task and exchanges JSON with the PowerShell helper. It has no
pywin32 dependency and remains import-safe on non-Windows test hosts.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Callable


OUTLOOK_ATTACHMENT_ADAPTER = "outlook_attachment"
# Worksheet-oriented flat-file formats that the worker can normalize to CSV.
# Macro-capable formats are read as values only; Metronome never executes VBA.
# Excel add-ins (.xla/.xlam/.xll) are deliberately excluded because they are
# programs, not a reliable tabular workbook contract.
SUPPORTED_ATTACHMENT_EXTENSIONS = (
    ".csv",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".xlt",
    ".xltm",
    ".xltx",
)
SUPPORTED_ATTACHMENT_EXTENSION_SET = frozenset(SUPPORTED_ATTACHMENT_EXTENSIONS)
_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "outlook_flow_attachment.ps1"
_EMBEDDED_SCRIPT = None  # Filled by the portable source builder, never from user input.
_IDENTITY_PART_SEPARATOR = "\x1f"
_TASK_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class OutlookAcquisitionError(RuntimeError):
    pass


def attachment_identity(store_id: str, entry_id: str, attachment_index: int, name: str) -> str:
    canonical = _IDENTITY_PART_SEPARATOR.join(
        (str(store_id), str(entry_id), str(int(attachment_index)), str(name))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _task_name(run_id: int | str) -> str:
    safe = _TASK_SAFE.sub("_", str(run_id)).strip("_") or "run"
    return f"Metronome_Outlook_Flow_{safe}"[:220]


def _run_command(args: list[str], timeout: float, *, check: bool = True):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=check,
    )


def acquire_attachment(
    *,
    run_id: int,
    profile_dir: Path,
    subject_contains: str,
    last_processed_identity: str | None,
    force_reprocess: bool,
    timeout_seconds: float = 180,
    command_runner: Callable[..., object] = _run_command,
) -> dict:
    """Find and save the newest qualifying Inbox attachment.

    Returns ``saved``, ``no_match``, or ``already_processed``. A saved result
    points to a local staging file; the normal Flow storage contract moves and
    validates that file after the run folder has been registered.
    """
    if platform.system() != "Windows":
        raise OutlookAcquisitionError("Outlook attachment acquisition is only available on Windows.")
    if _EMBEDDED_SCRIPT is None and not _SCRIPT.is_file():
        raise OutlookAcquisitionError(f"Outlook acquisition helper is missing: {_SCRIPT}")
    needle = str(subject_contains or "").strip()
    if not needle:
        raise OutlookAcquisitionError("The Outlook subject search text is empty.")

    exchange_dir = Path(profile_dir).resolve() / "outlook_downloads" / f"run-{int(run_id)}"
    staging_dir = exchange_dir / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    helper = _SCRIPT
    if _EMBEDDED_SCRIPT is not None:
        helper = exchange_dir / 'outlook_flow_attachment.ps1'
        with helper.open('x', encoding='utf-8-sig') as handle:
            handle.write(_EMBEDDED_SCRIPT)
    request_path = exchange_dir / "request.json"
    result_path = exchange_dir / "result.json"
    launcher_path = exchange_dir / "launch.ps1"
    if result_path.exists():
        result_path.unlink()
    request_path.write_text(
        json.dumps(
            {
                "subject_contains": needle,
                "last_processed_identity": last_processed_identity or None,
                "force_reprocess": bool(force_reprocess),
                "output_folder": str(staging_dir),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # schtasks.exe limits the /TR command length. Keep that command short and
    # put the longer repository/request/result paths into a per-run launcher.
    ps_literal = lambda value: str(value).replace("'", "''")
    launcher_path.write_text(
        "& '" + ps_literal(helper) + "' "
        "-RequestPath '" + ps_literal(request_path) + "' "
        "-ResultPath '" + ps_literal(result_path) + "'\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )

    task_name = _task_name(run_id)
    task_command = (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{launcher_path}"'
    )
    schtasks = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "schtasks.exe")
    try:
        command_runner(
            [
                schtasks, "/create", "/tn", task_name, "/tr", task_command,
                "/sc", "once", "/st", "00:00", "/it", "/f",
            ],
            15,
        )
        command_runner([schtasks, "/run", "/tn", task_name], 15)
        deadline = time.monotonic() + max(1, timeout_seconds)
        while time.monotonic() < deadline:
            if result_path.is_file():
                break
            time.sleep(0.25)
        else:
            raise OutlookAcquisitionError(
                f"Outlook did not return an attachment result within {int(timeout_seconds)} seconds."
            )
    except subprocess.CalledProcessError as exc:
        detail = (getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)).strip()
        raise OutlookAcquisitionError(f"Could not launch the interactive Outlook task: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OutlookAcquisitionError("Windows timed out while launching the interactive Outlook task.") from exc
    finally:
        try:
            command_runner([schtasks, "/delete", "/tn", task_name, "/f"], 15, check=False)
        except Exception:
            pass

    try:
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise OutlookAcquisitionError("Outlook returned an unreadable acquisition result.") from exc
    status = str(result.get("status") or "").casefold()
    if status == "error":
        raise OutlookAcquisitionError(str(result.get("error") or "Outlook attachment acquisition failed."))
    if status in {"no_match", "already_processed"}:
        return {"status": status, "message": str(result.get("message") or "")}
    if status != "saved":
        raise OutlookAcquisitionError(f"Outlook returned an unsupported result status: {status or 'blank'}")

    original_name = str(result.get("attachment_name") or "")
    name = Path(str(result.get("saved_name") or "")).name
    saved_path = Path(str(result.get("saved_path") or "")).resolve()
    try:
        saved_path.relative_to(staging_dir.resolve())
    except ValueError as exc:
        raise OutlookAcquisitionError("Outlook returned an attachment path outside its staging folder.") from exc
    if not name or saved_path.name != name or not saved_path.is_file():
        raise OutlookAcquisitionError("Outlook did not save the selected attachment safely.")
    if saved_path.suffix.casefold() not in SUPPORTED_ATTACHMENT_EXTENSION_SET:
        raise OutlookAcquisitionError("Outlook selected an unsupported attachment type.")

    try:
        index = int(result["attachment_index"])
        identity = attachment_identity(
            result["store_id"], result["entry_id"], index, original_name
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OutlookAcquisitionError("Outlook returned an incomplete attachment identity.") from exc
    if result.get("identity") and str(result["identity"]).casefold() != identity:
        raise OutlookAcquisitionError("Outlook attachment identity verification failed.")

    receipt = {
        "identity": identity,
        "received_at": str(result.get("received_at") or "")[:100] or None,
        "attachment_name": Path(original_name).name[:500] or name[:500],
        "subject": str(result.get("subject") or "")[:1000] or None,
    }
    return {
        "status": "saved",
        "path": saved_path,
        "filename": name,
        "receipt": receipt,
    }
