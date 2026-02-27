from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import ActionOut, ActionUpdate

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.get("", response_model=list[ActionOut])
def list_actions(status: str | None = None):
    with get_db() as db:
        if status:
            rows = db.execute("""
                SELECT a.*, s.name AS source_name, r.name AS report_name
                FROM actions a
                LEFT JOIN sources s ON s.id = a.source_id
                LEFT JOIN reports r ON r.id = a.report_id
                ORDER BY a.created_at DESC
            """).fetchall()
            rows = [r for r in rows if r["status"] == status]
        else:
            rows = db.execute("""
                SELECT a.*, s.name AS source_name, r.name AS report_name
                FROM actions a
                LEFT JOIN sources s ON s.id = a.source_id
                LEFT JOIN reports r ON r.id = a.report_id
                ORDER BY a.created_at DESC
            """).fetchall()

    return [
        ActionOut(
            id=r["id"],
            source_id=r["source_id"],
            source_name=r["source_name"],
            report_id=r["report_id"],
            report_name=r["report_name"],
            type=r["type"],
            status=r["status"],
            assigned_to=r["assigned_to"],
            notes=r["notes"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            resolved_at=r["resolved_at"],
        )
        for r in rows
    ]


@router.patch("/{action_id}", response_model=ActionOut)
def update_action(action_id: int, update: ActionUpdate):
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        existing = db.execute("SELECT id FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Action not found")

        fields = ["updated_at = ?"]
        values = [now]

        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        # Auto-set resolved_at when status becomes resolved or expected
        if update.status in ("resolved", "expected"):
            fields.append("resolved_at = ?")
            values.append(now)

        values.append(action_id)
        db.execute(
            f"UPDATE actions SET {', '.join(fields)} WHERE id = ?",
            values,
        )

        r = db.execute("""
            SELECT a.*, s.name AS source_name, r.name AS report_name
            FROM actions a
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN reports r ON r.id = a.report_id
            WHERE a.id = ?
        """, (action_id,)).fetchone()

    return ActionOut(
        id=r["id"],
        source_id=r["source_id"],
        source_name=r["source_name"],
        report_id=r["report_id"],
        report_name=r["report_name"],
        type=r["type"],
        status=r["status"],
        assigned_to=r["assigned_to"],
        notes=r["notes"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        resolved_at=r["resolved_at"],
    )
