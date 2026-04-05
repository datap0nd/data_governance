"""Scripts API - scan, list, update, and delete Python scripts."""

from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.routers.eventlog import log_event
from app.models import ScriptOut, ScriptUpdate, ScriptTableOut
from app.scanner.script_runner import run_script_scan

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


def _build_script_out(db, row) -> ScriptOut:
    """Build a ScriptOut model from a script row, attaching table references."""
    script_id = row["id"]
    table_rows = db.execute(
        "SELECT table_name, direction FROM script_tables WHERE script_id = ?",
        (script_id,),
    ).fetchall()

    tables_read = [t["table_name"] for t in table_rows if t["direction"] == "read"]
    tables_written = [t["table_name"] for t in table_rows if t["direction"] == "write"]

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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[ScriptOut])
def list_scripts():
    with get_db() as db:
        rows = db.execute("SELECT * FROM scripts ORDER BY display_name").fetchall()
        return [_build_script_out(db, r) for r in rows]


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
def trigger_script_scan():
    """Trigger a scan of the scripts directory."""
    result = run_script_scan()
    return result


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
