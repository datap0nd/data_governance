"""Managed Flow paths. This is path containment, not process isolation."""
from __future__ import annotations

import ntpath
import os
import re
from pathlib import Path

SOURCE_FOLDERS = {
    "asap_portal": "ASAP", "gscm_portal": "GSCM",
    "outlook_attachment": "Outlook", "local_file": "Local", "web_export": "Web",
}


class PathOutsideRoot(ValueError):
    pass


def setting(db, key: str, default=None):
    row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(db, key: str, value):
    db.execute("""INSERT INTO app_settings(key,value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
        (key, str(value)))


def default_flows_root(db=None) -> str:
    from app import database
    filename = database.DB_PATH
    if db is not None:
        filename = db.execute("PRAGMA database_list").fetchone()[2] or filename
    return str(Path(os.path.abspath(filename)).parent / "metronome" / "flows")


def get_flows_root(db) -> str:
    return setting(db, "flows_root") or os.environ.get("DG_FLOWS_ROOT") or default_flows_root(db)


def source_folder_name(adapter: str) -> str:
    if adapter not in SOURCE_FOLDERS:
        raise PathOutsideRoot("Unsupported Flow source adapter.")
    return SOURCE_FOLDERS[adapter]


def clean_absolute(value: str) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("\\\\?\\unc\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    if not raw or raw.startswith(("\\\\.\\", "\\??\\")) or any(ord(c) < 32 for c in raw):
        raise PathOutsideRoot("Choose an absolute filesystem path, not a device path.")
    windows = bool(ntpath.splitdrive(raw)[0]) or "\\" in raw
    parts = re.split(r"[\\/]", raw)
    if ".." in parts:
        raise PathOutsideRoot("Path traversal (..) is not allowed.")
    if windows:
        drive, tail = ntpath.splitdrive(raw)
        if not drive or not tail.startswith(("\\", "/")) or ":" in tail:
            raise PathOutsideRoot("Choose a fully qualified path without alternate streams.")
        if os.name != "nt":
            raise PathOutsideRoot("This Windows path must be configured on the Windows worker host.")
        if any(p.endswith((".", " ")) or re.search(r'[<>"|?*]', p) for p in re.split(r"[\\/]", tail) if p):
            raise PathOutsideRoot("Path contains an ambiguous Windows filename.")
    if not os.path.isabs(raw):
        raise PathOutsideRoot("Choose an absolute filesystem path.")
    return os.path.normpath(raw)


def is_inside(value: str, root: str, *, resolve=True) -> bool:
    try:
        path, parent = clean_absolute(value), clean_absolute(root)
        if resolve:
            path, parent = os.path.realpath(path), os.path.realpath(parent)
        path, parent = os.path.normcase(path), os.path.normcase(parent)
        return path != parent and os.path.commonpath([path, parent]) == parent
    except (OSError, ValueError):
        return False


def assert_inside(value: str, root: str, *, label="Path", resolve=True) -> str:
    if not is_inside(value, root, resolve=resolve):
        raise PathOutsideRoot(f"{label} must be inside {root}.")
    return clean_absolute(value)


def validate_root(value: str) -> str:
    root = clean_absolute(value)
    resolved = Path(root).resolve()
    code = Path(__file__).resolve().parent.parent
    if resolved == Path(resolved.anchor) or resolved == code or is_inside(str(resolved), str(code)):
        raise PathOutsideRoot("Flows root must be a dedicated folder outside the code checkout.")
    if resolved.exists() and not resolved.is_dir():
        raise PathOutsideRoot("Flows root is not a directory.")
    return str(resolved)


def policy(db, flow: dict) -> dict | None:
    enforced = setting(db, "flows_paths_enforced", "0") == "1"
    if not enforced and not flow.get("flow_folder"):
        return None
    root = get_flows_root(db)
    return {"flows_root": root, "source_folder": source_folder_name(flow["source_adapter"]),
            "enforced": enforced, "flow_folder": flow.get("flow_folder"), "version": 1}


def validate_flow(flow: dict, rules: dict | None, *, resolve=True):
    if not rules:
        return
    root = rules["flows_root"]
    source = str(Path(root) / rules["source_folder"])
    assert_inside(source, root, label="Source folder", resolve=resolve)
    if flow.get("source_type") != "file":
        assert_inside(flow.get("target_folder"), source, label="Target folder", resolve=resolve)
    elif rules.get("enforced"):
        assert_inside(flow.get("local_file_path"), str(Path(root) / "Local"), label="Source file", resolve=resolve)
    if flow.get("transform_enabled") and rules.get("enforced"):
        assert_inside(flow.get("transform_script_path"), root, label="Transformation script", resolve=resolve)


def assert_job_paths(job: dict):
    rules = job.get("paths")
    if not rules:
        return
    if rules.get("version") != 1:
        raise PathOutsideRoot("Unsupported Flow path policy version.")
    validate_flow({**job.get("flow", {}),
        "target_folder": job.get("downloads", {}).get("target_folder"),
        "local_file_path": job.get("local_file", {}).get("path"),
        "transform_enabled": job.get("transformation", {}).get("enabled"),
        "transform_script_path": job.get("transformation", {}).get("script_path")}, rules)
    for section in ("resume", "sql_retry"):
        for item in (job.get(section) or {}).get("artifacts", []):
            # Legacy private recovery has its own exact store/checksum protocol.
            if item.get("storage_scope") != "worker_private":
                assert_inside(item.get("file_path"), rules["flows_root"], label="Recovery artifact")
