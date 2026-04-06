"""Scripts API - scan, list, update, and delete Python scripts."""

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from app.database import get_db
from app.routers.eventlog import log_event
from app.models import ScriptOut, ScriptUpdate, ScriptTableOut
from app.scanner.script_runner import run_script_scan

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

# In-memory scan state for async scanning with live log
_scan_state = {
    "status": "idle",  # idle | running | completed | failed
    "log": [],
    "started_at": None,
    "finished_at": None,
    "result": None,
}
_scan_lock = threading.Lock()


def _append_log(message: str):
    """Append a timestamped log line to the scan state."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    _scan_state["log"].append(f"[{ts}] {message}")


def _run_scan_background(new_only: bool = False):
    """Run script scan in background thread, updating _scan_state."""
    try:
        result = run_script_scan(on_progress=_append_log, new_only=new_only)
        _scan_state["result"] = result
        _scan_state["status"] = "completed" if result.get("status") == "completed" else "failed"
        _append_log(f"Scan finished: {result.get('scripts_total', 0)} scripts, {result.get('tables_linked', 0)} tables linked")
    except Exception as e:
        _scan_state["status"] = "failed"
        _scan_state["result"] = {"status": "failed", "error": str(e)}
        _append_log(f"ERROR: {e}")
    finally:
        _scan_state["finished_at"] = datetime.now(timezone.utc).isoformat()


def _build_script_out(db, row) -> ScriptOut:
    """Build a ScriptOut model from a script row, attaching table references."""
    script_id = row["id"]
    table_rows = db.execute(
        "SELECT table_name, direction FROM script_tables WHERE script_id = ?",
        (script_id,),
    ).fetchall()

    tables_read = [t["table_name"] for t in table_rows if t["direction"] == "read"]
    tables_written = [t["table_name"] for t in table_rows if t["direction"] == "write"]

    keys = row.keys()
    return ScriptOut(
        id=row["id"],
        path=row["path"],
        display_name=row["display_name"],
        owner=row["owner"],
        last_modified=row["last_modified"],
        last_scanned=row["last_scanned"],
        file_size=row["file_size"],
        tables_read=sorted(tables_read),
        tables_written=sorted(tables_written),
        hostname=row["hostname"] if "hostname" in keys else None,
        machine_alias=row["machine_alias"] if "machine_alias" in keys else None,
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[ScriptOut])
def list_scripts(include_archived: bool = Query(False)):
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE archived = 0"
        rows = db.execute(f"SELECT * FROM scripts {archive_filter} ORDER BY display_name").fetchall()
        return [_build_script_out(db, r) for r in rows]


@router.get("/scan/status")
def get_scan_status():
    """Get current script scan status and log lines."""
    return {
        "status": _scan_state["status"],
        "log": _scan_state["log"],
        "started_at": _scan_state["started_at"],
        "finished_at": _scan_state["finished_at"],
        "result": _scan_state["result"],
    }


@router.get("/{script_id}", response_model=ScriptOut)
def get_script(script_id: int):
    with get_db() as db:
        r = db.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Script not found")
        return _build_script_out(db, r)


@router.patch("/{script_id}", response_model=ScriptOut)
def update_script(script_id: int, update: ScriptUpdate):
    with get_db() as db:
        existing = db.execute("SELECT id FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Script not found")

        fields = []
        values = []
        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(script_id)
            db.execute(
                f"UPDATE scripts SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            changed = ", ".join(k for k in update.model_dump(exclude_unset=True))
            log_event(db, "script", script_id, None, "updated", changed)

    return get_script(script_id)


@router.delete("/{script_id}")
def delete_script(script_id: int):
    with get_db() as db:
        row = db.execute("SELECT id, display_name FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Script not found")
        db.execute("DELETE FROM script_tables WHERE script_id = ?", (script_id,))
        db.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        log_event(db, "script", script_id, row["display_name"], "deleted")
    return {"status": "deleted", "id": script_id}


@router.post("/scan")
def trigger_script_scan(new_only: bool = Query(False)):
    """Trigger an async scan of the scripts directory."""
    with _scan_lock:
        if _scan_state["status"] == "running":
            return {"status": "already_running", "log": _scan_state["log"]}

        _scan_state["status"] = "running"
        _scan_state["log"] = []
        _scan_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _scan_state["finished_at"] = None
        _scan_state["result"] = None

    mode = "new only" if new_only else "full"
    _append_log(f"Scan started ({mode})")
    thread = threading.Thread(target=_run_scan_background, args=(new_only,), daemon=True)
    thread.start()

    return {"status": "started"}


@router.get("/{script_id}/tables", response_model=list[ScriptTableOut])
def get_script_tables(script_id: int):
    with get_db() as db:
        existing = db.execute("SELECT id FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Script not found")

        rows = db.execute("""
            SELECT st.*, s.name AS source_name
            FROM script_tables st
            LEFT JOIN sources s ON s.id = st.source_id
            WHERE st.script_id = ?
            ORDER BY st.direction, st.table_name
        """, (script_id,)).fetchall()

    return [
        ScriptTableOut(
            id=r["id"],
            script_id=r["script_id"],
            table_name=r["table_name"],
            direction=r["direction"],
            source_id=r["source_id"],
            source_name=r["source_name"],
        )
        for r in rows
    ]
