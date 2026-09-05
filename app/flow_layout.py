"""Owned per-flow folders; display names never determine recovery identity."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import flow_paths

MANIFEST_NAME = "flow.json"
LAYOUT_VERSION = 1


def flow_folder_slug(name: str, flow_id: int) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")[:72].rstrip(". ") or "Flow"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name.split(".")[0]):
        name = "Flow " + name
    return f"{name} (id {flow_id})"


def _regular(path: Path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError(f"Managed path is a link: {path}")


def read_manifest(folder: str | Path, flow_id: int) -> dict:
    folder = Path(folder)
    _regular(folder)
    marker = folder / MANIFEST_NAME
    _regular(marker)
    data = json.loads(marker.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema") != "metronome-flow-folder"
            or data.get("layout_version") != LAYOUT_VERSION or data.get("flow_id") != flow_id):
        raise ValueError("Folder belongs to another flow or uses an unsupported layout.")
    return data


def write_manifest(folder: str | Path, data: dict):
    folder = Path(folder)
    _regular(folder)
    _regular(folder / MANIFEST_NAME)
    temp = folder / f".flow-{uuid.uuid4()}.tmp"
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, folder / MANIFEST_NAME)
    finally:
        temp.unlink(missing_ok=True)


def update_manifest(folder: str | Path, flow_id: int, **values):
    data = read_manifest(folder, flow_id)
    data.update(values, updated_at=datetime.now(timezone.utc).isoformat())
    write_manifest(folder, data)


def cleanup_empty_creation(folder: str | Path, flow_id: int):
    """Compensate only our empty allocation; never recursively remove content."""
    folder = Path(folder)
    try:
        read_manifest(folder, flow_id)
        for child in folder.iterdir():
            if child.name == MANIFEST_NAME:
                continue
            _regular(child)
            if child.name not in {"Downloads", "Scripts"} or not child.is_dir() or any(child.iterdir()):
                return
        for name in ("Downloads", "Scripts"):
            child = folder / name
            if child.exists():
                child.rmdir()
        (folder / MANIFEST_NAME).unlink()
        folder.rmdir()
    except (OSError, ValueError):
        # A later operator can inspect an orphan; never guess ownership.
        return


def create_flow_folder(root: str, adapter: str, name: str, flow_id: int) -> Path:
    root = flow_paths.validate_root(root)
    source = Path(root) / flow_paths.source_folder_name(adapter)
    flow_paths.assert_inside(str(source), root, label="Source folder")
    source.mkdir(parents=True, exist_ok=True)
    folder = source / flow_folder_slug(name, flow_id)
    flow_paths.assert_inside(str(folder), root, label="Flow folder")
    folder.mkdir()  # Exclusive: never attach to an existing user's directory.
    now = datetime.now(timezone.utc).isoformat()
    try:
        write_manifest(folder, {"schema": "metronome-flow-folder", "layout_version": LAYOUT_VERSION,
            "flow_id": flow_id, "flow_name": name, "source_adapter": adapter,
            "created_at": now, "updated_at": now, "deleted_at": None})
        (folder / "Downloads").mkdir()
        (folder / "Scripts").mkdir()
    except Exception:
        cleanup_empty_creation(folder, flow_id)
        # If marker creation itself failed, only an empty directory is removable.
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
    return folder
