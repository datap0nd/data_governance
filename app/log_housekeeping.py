"""Keep the services' log folder bounded.

setup.ps1 points every Windows service's stdout and stderr at
``<project>/logs``. nssm rotates a log once it reaches 10 MB, renaming it
``<name>-<YYYYMMDDTHHMMSS.mmm>.log``, and never deletes a rotated file. This
module does: first every log nobody has written to for longer than the keep
period, then rotated logs oldest first until the folder fits its size limit.

A log a service is writing to is never deleted to make room. On Windows it
cannot be (the service holds it open); the size pass skips it by name so the
rule is the same everywhere.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config

logger = logging.getLogger("log_housekeeping")

# Stored in app_settings; the Settings page edits the same keys.
KEEP_DAYS_KEY = "logs.keep_days"
MAX_TOTAL_MB_KEY = "logs.max_total_mb"
DEFAULT_KEEP_DAYS = 14
DEFAULT_MAX_TOTAL_MB = 1024
KEEP_DAYS_RANGE = (1, 365)
MAX_TOTAL_MB_RANGE = (50, 102400)
# Update transcripts and receipts are small; keep the newest ones regardless
# of age so the Updates page always has recent history to show.
UPDATE_FILES_KEEP = 20

# nssm's rotated name: <original stem>-<UTC timestamp with milliseconds>.log
ROTATED = re.compile(r"^(?P<stem>.+)-\d{8}T\d{6}\.\d{3}\.log$")

_run_lock = threading.Lock()


def log_directory() -> Path:
    """Where setup.ps1 sends service output: ``logs`` beside the code folder."""
    override = os.environ.get("DG_LOG_DIR")
    return Path(override) if override else config.BASE_DIR.parent / "logs"


def update_directory() -> Path:
    return config.BASE_DIR.parent / "updates"


def _bounded_setting(key: str, default: int, bounds: tuple[int, int]) -> int:
    from app.settings import get_setting

    try:
        value = int(get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return default
    except Exception:  # the database is busy or absent; the default still applies
        return default
    return min(bounds[1], max(bounds[0], value))


def keep_days() -> int:
    return _bounded_setting(KEEP_DAYS_KEY, DEFAULT_KEEP_DAYS, KEEP_DAYS_RANGE)


def max_total_mb() -> int:
    return _bounded_setting(MAX_TOTAL_MB_KEY, DEFAULT_MAX_TOTAL_MB, MAX_TOTAL_MB_RANGE)


def _log_files(directory: Path) -> list[dict]:
    """Regular ``*.log`` files directly inside ``directory``; never a link."""
    files = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return files
    for entry in entries:
        try:
            if not entry.name.lower().endswith(".log") or not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        files.append({
            "name": entry.name,
            "path": Path(entry.path),
            "size": int(stat.st_size),
            "modified": float(stat.st_mtime),
            "rotated": bool(ROTATED.match(entry.name)),
        })
    return files


def summary(directory: Path | None = None, *, largest: int = 10) -> dict:
    """What the log folder holds now, for the Settings page."""
    directory = Path(directory) if directory is not None else log_directory()
    files = _log_files(directory)
    ordered = sorted(files, key=lambda item: item["size"], reverse=True)
    return {
        "directory": str(directory),
        "exists": directory.is_dir(),
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "rotated_bytes": sum(item["size"] for item in files if item["rotated"]),
        "largest": [
            {
                "name": item["name"],
                "bytes": item["size"],
                "rotated": item["rotated"],
                "modified_at": datetime.fromtimestamp(item["modified"], timezone.utc).isoformat(timespec="seconds"),
            }
            for item in ordered[:largest]
        ],
    }


def _delete(item: dict, result: dict) -> bool:
    try:
        item["path"].unlink()
    except FileNotFoundError:
        return True
    except OSError as exc:
        # A service still has the file open (Windows), or permissions differ.
        result["skipped"].append({"name": item["name"], "reason": type(exc).__name__})
        return False
    result["deleted_files"] += 1
    result["deleted_bytes"] += item["size"]
    return True


def prune(
    directory: Path | None = None,
    *,
    keep_days: int,
    max_total_bytes: int,
    now: datetime | None = None,
) -> dict:
    """Delete old logs, then rotated logs until the folder fits ``max_total_bytes``.

    ``keep_days`` and ``max_total_bytes`` of 0 switch that rule off. Returns
    what was deleted and what could not be.
    """
    directory = Path(directory) if directory is not None else log_directory()
    result = {
        "directory": str(directory), "deleted_files": 0, "deleted_bytes": 0,
        "skipped": [], "remaining_bytes": 0, "over_limit": False,
    }
    if not directory.is_dir():
        return result
    now = now or datetime.now(timezone.utc)
    files = _log_files(directory)
    kept = []
    if keep_days > 0:
        cutoff = (now - timedelta(days=keep_days)).timestamp()
        for item in files:
            if item["modified"] < cutoff and _delete(item, result):
                continue
            kept.append(item)
    else:
        kept = files
    if max_total_bytes > 0:
        total = sum(item["size"] for item in kept)
        for item in sorted((entry for entry in kept if entry["rotated"]), key=lambda entry: entry["modified"]):
            if total <= max_total_bytes:
                break
            if _delete(item, result):
                total -= item["size"]
                kept.remove(item)
        result["over_limit"] = total > max_total_bytes
    result["remaining_bytes"] = sum(item["size"] for item in kept)
    return result


def _prune_update_files(root: Path, *, keep_days: int, now: datetime) -> int:
    """Old update transcripts and receipts, always keeping the newest few."""
    deleted = 0
    cutoff = (now - timedelta(days=keep_days)).timestamp()
    for folder, suffix in (("logs", ".log"), ("receipts", ".json")):
        directory = root / folder
        try:
            entries = [entry for entry in os.scandir(directory)
                       if entry.name.lower().endswith(suffix) and entry.is_file(follow_symlinks=False)]
        except OSError:
            continue
        entries.sort(key=lambda entry: entry.stat(follow_symlinks=False).st_mtime, reverse=True)
        for entry in entries[UPDATE_FILES_KEEP:]:
            try:
                if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                    os.unlink(entry.path)
                    deleted += 1
            except OSError:
                continue
    return deleted


def run(now: datetime | None = None) -> dict | None:
    """Apply the configured limits once. Safe to call from a scheduler."""
    if not _run_lock.acquire(blocking=False):
        return None
    try:
        now = now or datetime.now(timezone.utc)
        days = keep_days()
        result = prune(keep_days=days, max_total_bytes=max_total_mb() * 1024 * 1024, now=now)
        result["update_files_deleted"] = _prune_update_files(update_directory(), keep_days=days, now=now)
        if result["deleted_files"] or result["update_files_deleted"]:
            logger.info(
                "Deleted %d log file(s), %.1f MB; %.1f MB of logs remain",
                result["deleted_files"], result["deleted_bytes"] / 1_048_576,
                result["remaining_bytes"] / 1_048_576,
            )
        if result["over_limit"]:
            logger.warning(
                "Logs still exceed the size limit: %.1f MB is in files the services are writing to; "
                "they rotate once they reach 10 MB or when the service next starts",
                result["remaining_bytes"] / 1_048_576,
            )
        return result
    except Exception:
        logger.exception("Log clean-up failed")
        return None
    finally:
        _run_lock.release()
