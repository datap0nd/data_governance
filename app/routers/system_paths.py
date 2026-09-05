"""Explicit configuration and migration diagnostics for Flow paths."""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import flow_paths
from app.database import get_db
from app.local_access import require_app_access
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/system/paths", tags=["paths"])


class PathsWrite(BaseModel):
    flows_root: str
    create: bool = False
    enforced: bool = False


def paths_state(db, root=None, *, resolve=False):
    root = root or flow_paths.get_flows_root(db)
    outside = []
    for row in db.execute("SELECT f.*, s.adapter AS source_adapter FROM flows f JOIN flow_sites s ON s.id=f.site_id"):
        flow = dict(row)
        rules = {"flows_root": root, "source_folder": flow_paths.source_folder_name(flow["source_adapter"]), "enforced": True}
        try:
            flow_paths.validate_flow(flow, rules, resolve=resolve)
        except ValueError as exc:
            outside.append({"id": flow["id"], "name": flow["name"], "reason": str(exc), "target_folder": flow["target_folder"] if flow["source_type"] != "file" else flow["local_file_path"]})
    return {"flows_root": root, "default": flow_paths.default_flows_root(db),
            "source": "setting" if flow_paths.setting(db, "flows_root") else "env" if os.environ.get("DG_FLOWS_ROOT") else "default",
            "enforced": flow_paths.setting(db, "flows_paths_enforced", "0") == "1",
            "source_folders": [{"adapter": a, "name": n, "path": str(Path(root) / n)} for a, n in flow_paths.SOURCE_FOLDERS.items()],
            "flows_outside_root": outside}


@router.get("")
def get_paths():
    with get_db() as db:
        return paths_state(db)


@router.post("/validate")
def validate_paths(body: PathsWrite, request: Request):
    require_app_access(request)
    try:
        root = flow_paths.validate_root(body.flows_root)
        with get_db() as db:
            return {"ok": True, **paths_state(db, root, resolve=True)}
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("")
def put_paths(body: PathsWrite, request: Request):
    require_app_access(request)
    try:
        root = flow_paths.validate_root(body.flows_root)
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = paths_state(db)
            if root != old["flows_root"] or body.enforced != old["enforced"]:
                active = db.execute("SELECT id FROM flow_runs WHERE status IN ('queued','claimed','running')").fetchall()
                if active:
                    raise HTTPException(409, "Wait for queued and active Flow runs to finish before changing paths.")
            if body.create:
                Path(root).mkdir(parents=True, exist_ok=True)
                for name in flow_paths.SOURCE_FOLDERS.values():
                    folder = str(Path(root) / name)
                    flow_paths.assert_inside(folder, root)
                    Path(folder).mkdir(exist_ok=True)
            flow_paths.save_setting(db, "flows_root", root)
            flow_paths.save_setting(db, "flows_paths_enforced", int(body.enforced))
            log_event(db, "system", None, "Flow paths", "updated", f"enforced={body.enforced}", get_actor(request))
            return paths_state(db)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
