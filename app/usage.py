"""Power BI usage CSV import and impact calculations."""

from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlite3 import Connection

from app.config import USAGE_FILES_PATH

logger = logging.getLogger(__name__)

REPORTS_CSV = "Reports.csv"
REPORT_VIEWS_CSV = "Report_views.csv"
USERS_CSV = "Users.csv"
PREMIUM_VIEW_WEIGHT = 5

_LAST_CSV_SIGNATURE: tuple | None = None


def _configured_dir() -> Path | None:
    if not USAGE_FILES_PATH:
        return None
    return Path(USAGE_FILES_PATH).expanduser()


def _usage_files(base: Path) -> dict[str, Path]:
    return {
        "reports": base / REPORTS_CSV,
        "report_views": base / REPORT_VIEWS_CSV,
        "users": base / USERS_CSV,
    }


def _file_signature(files: dict[str, Path]) -> tuple:
    signature = []
    for key, path in sorted(files.items()):
        if not path.exists():
            signature.append((key, None, None))
            continue
        stat = path.stat()
        signature.append((key, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _header_key(value: str | None) -> str:
    raw = (value or "").strip().lstrip("\ufeff")
    if "[" in raw and raw.endswith("]"):
        raw = raw.rsplit("[", 1)[1][:-1]
    return re.sub(r"[^a-z0-9]", "", raw.lower())


def _row_lookup(row: dict) -> dict[str, str]:
    return {_header_key(k): (v or "").strip() for k, v in row.items()}


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        val = row.get(_header_key(name))
        if val:
            return val
    return ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1252", "utf-8"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                return [_row_lookup(row) for row in reader]
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _norm_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _norm_user_id(value: str | None) -> str:
    return (value or "").strip().lower()


def _db_report_map(db: Connection) -> dict[str, int]:
    rows = db.execute("SELECT id, name FROM reports").fetchall()
    return {_norm_name(r["name"]): r["id"] for r in rows if r["name"]}


def sync_usage_from_csv(db: Connection, force: bool = False) -> dict:
    """Import last-30-days usage from usage_files_path CSVs.

    The window is anchored to the latest Date found in Report_views.csv,
    matching the user's "last 30d in the CSV" requirement.
    """
    global _LAST_CSV_SIGNATURE

    base = _configured_dir()
    if not base:
        return {"status": "skipped", "message": "usage_files_path is not configured"}
    if not base.exists():
        return {"status": "error", "message": f"Usage folder not found: {base}"}

    files = _usage_files(base)
    report_views_path = files["report_views"]
    if not report_views_path.exists():
        return {"status": "error", "message": f"Usage file not found: {report_views_path}"}

    signature = _file_signature(files)
    if not force and signature == _LAST_CSV_SIGNATURE:
        return {"status": "unchanged", "message": "Usage CSVs unchanged"}

    reports_by_guid: dict[str, str] = {}
    if files["reports"].exists():
        for row in _read_csv(files["reports"]):
            guid = _value(row, "ReportGuid")
            name = _value(row, "ReportName")
            if guid and name:
                reports_by_guid[guid.strip().lower()] = name

    users_by_key: dict[str, str] = {}
    if files["users"].exists():
        for row in _read_csv(files["users"]):
            user_key = _value(row, "UserKey")
            user_id = _value(row, "UserId")
            if user_key and user_id:
                users_by_key[user_key.strip().lower()] = user_id

    view_rows = _read_csv(report_views_path)
    dated_rows: list[tuple[dict[str, str], date]] = []
    for row in view_rows:
        view_date = _parse_date(_value(row, "Date"))
        if view_date:
            dated_rows.append((row, view_date))

    if not dated_rows:
        return {"status": "error", "message": "No dated report views found in Report_views.csv"}

    latest = max(view_date for _, view_date in dated_rows)
    cutoff = latest - timedelta(days=29)
    report_name_to_id = _db_report_map(db)

    per_user: defaultdict[tuple[str, str, int | None, str, str], int] = defaultdict(int)
    matched_reports: set[int] = set()
    skipped_no_report = 0
    skipped_no_user = 0

    for row, view_date in dated_rows:
        if view_date < cutoff or view_date > latest:
            continue

        report_guid = _value(row, "ReportId").strip()
        report_name = reports_by_guid.get(report_guid.lower()) or _value(row, "ReportName")
        report_name = report_name.strip()
        if not report_name:
            skipped_no_report += 1
            continue

        user_key = _value(row, "UserKey").strip().lower()
        viewer = users_by_key.get(user_key) or _value(row, "UserId")
        viewer = _norm_user_id(viewer)
        if not viewer:
            skipped_no_user += 1
            continue

        report_id = report_name_to_id.get(_norm_name(report_name))
        if report_id:
            matched_reports.add(report_id)

        key = (report_guid, report_name, report_id, view_date.isoformat(), viewer)
        per_user[key] += 1

    now = datetime.now(timezone.utc).isoformat()
    db.execute("DELETE FROM pbi_report_user_views")
    db.execute("DELETE FROM pbi_report_views")
    db.execute("DELETE FROM pbi_usage_days")

    for (report_guid, report_name, report_id, view_date, viewer), count in per_user.items():
        db.execute(
            """INSERT INTO pbi_report_user_views
               (report_guid, report_name, report_id, user_id, view_date, view_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (report_guid, report_name, report_id, viewer, view_date, count),
        )

    per_report_day: dict[tuple[str, int | None, str], dict] = {}
    for (report_guid, report_name, report_id, view_date, viewer), count in per_user.items():
        aggregate = per_report_day.setdefault(
            (report_name, report_id, view_date),
            {"views": 0, "users": set()},
        )
        aggregate["views"] += count
        aggregate["users"].add(viewer)

    for (report_name, report_id, view_date), aggregate in per_report_day.items():
        db.execute(
            """INSERT INTO pbi_report_views
               (report_name, report_id, view_date, view_count, unique_users)
               VALUES (?, ?, ?, ?, ?)""",
            (report_name, report_id, view_date, aggregate["views"], len(aggregate["users"])),
        )

    for _, _, view_date in per_report_day:
        db.execute(
            "INSERT OR REPLACE INTO pbi_usage_days (date, synced_at) VALUES (?, ?)",
            (view_date, now),
        )

    _LAST_CSV_SIGNATURE = signature
    return {
        "status": "completed",
        "message": f"Imported {sum(per_user.values())} views from {cutoff.isoformat()} to {latest.isoformat()}",
        "views": sum(per_user.values()),
        "report_user_days": len(per_user),
        "matched_reports": len(matched_reports),
        "skipped_no_report": skipped_no_report,
        "skipped_no_user": skipped_no_user,
        "cutoff": cutoff.isoformat(),
        "latest_date": latest.isoformat(),
    }


def sync_usage_from_csv_if_configured(db: Connection) -> dict:
    """Best-effort CSV sync used by read endpoints."""
    try:
        result = sync_usage_from_csv(db, force=False)
        if result.get("status") == "error":
            logger.warning("Usage CSV sync skipped: %s", result.get("message"))
        return result
    except Exception as exc:
        logger.exception("Usage CSV sync failed")
        return {"status": "error", "message": str(exc)}


def _usage_cutoff(db: Connection) -> str | None:
    row = db.execute("SELECT MAX(view_date) AS max_date FROM pbi_report_user_views").fetchone()
    max_date = row["max_date"] if row else None
    if not max_date:
        row = db.execute("SELECT MAX(view_date) AS max_date FROM pbi_report_views").fetchone()
        max_date = row["max_date"] if row else None
    parsed = _parse_date(max_date)
    if not parsed:
        return None
    return (parsed - timedelta(days=29)).isoformat()


def get_report_usage_map(db: Connection) -> dict[int, dict[str, int]]:
    cutoff = _usage_cutoff(db)
    if not cutoff:
        return {}

    rows = db.execute(
        """SELECT v.report_id,
                  SUM(v.view_count) AS views_30d,
                  COUNT(DISTINCT v.user_id) AS unique_users_30d,
                  SUM(v.view_count * CASE WHEN pv.email IS NOT NULL THEN ? ELSE 1 END) AS impact_views_30d
           FROM pbi_report_user_views v
           LEFT JOIN premium_viewers pv ON LOWER(pv.email) = LOWER(v.user_id)
           WHERE v.view_date >= ? AND v.report_id IS NOT NULL
           GROUP BY v.report_id""",
        (PREMIUM_VIEW_WEIGHT, cutoff),
    ).fetchall()

    if rows:
        return {
            r["report_id"]: {
                "views_30d": int(r["views_30d"] or 0),
                "unique_users_30d": int(r["unique_users_30d"] or 0),
                "impact_views_30d": int(r["impact_views_30d"] or 0),
            }
            for r in rows
        }

    legacy_rows = db.execute(
        """SELECT report_id,
                  SUM(view_count) AS views_30d,
                  SUM(unique_users) AS unique_users_30d
           FROM pbi_report_views
           WHERE view_date >= ? AND report_id IS NOT NULL
           GROUP BY report_id""",
        (cutoff,),
    ).fetchall()
    return {
        r["report_id"]: {
            "views_30d": int(r["views_30d"] or 0),
            "unique_users_30d": int(r["unique_users_30d"] or 0),
            "impact_views_30d": int(r["views_30d"] or 0),
        }
        for r in legacy_rows
    }


def get_source_usage_map(db: Connection) -> dict[int, dict[str, int]]:
    report_usage = get_report_usage_map(db)
    if not report_usage:
        return {}

    rows = db.execute(
        """SELECT DISTINCT source_id, report_id
           FROM report_tables
           WHERE source_id IS NOT NULL AND report_id IS NOT NULL"""
    ).fetchall()

    source_usage: dict[int, dict[str, int]] = {}
    for row in rows:
        source_id = row["source_id"]
        report_id = row["report_id"]
        usage = report_usage.get(report_id)
        if not usage:
            continue
        target = source_usage.setdefault(
            source_id,
            {"views_30d": 0, "unique_users_30d": 0, "impact_views_30d": 0},
        )
        target["views_30d"] += usage["views_30d"]
        target["unique_users_30d"] += usage["unique_users_30d"]
        target["impact_views_30d"] += usage["impact_views_30d"]

    # Unique users across source-fed reports require the per-user table.
    cutoff = _usage_cutoff(db)
    if cutoff:
        user_rows = db.execute(
            """SELECT rt.source_id, COUNT(DISTINCT v.user_id) AS unique_users
               FROM (SELECT DISTINCT source_id, report_id
                     FROM report_tables
                     WHERE source_id IS NOT NULL AND report_id IS NOT NULL) rt
               JOIN pbi_report_user_views v ON v.report_id = rt.report_id
               WHERE v.view_date >= ?
               GROUP BY rt.source_id""",
            (cutoff,),
        ).fetchall()
        for row in user_rows:
            if row["source_id"] in source_usage:
                source_usage[row["source_id"]]["unique_users_30d"] = int(row["unique_users"] or 0)

    return source_usage
