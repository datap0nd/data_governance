"""People management - BI, PM, Management roles."""

from fastapi import APIRouter, HTTPException

from app.database import get_db
from app.routers.eventlog import log_event
from app.models import PersonOut, PersonCreate

router = APIRouter(prefix="/api/people", tags=["people"])

VALID_ROLES = ["BI", "PM", "Management"]


@router.get("/roles")
def get_roles():
    """Return the list of valid roles."""
    return VALID_ROLES


@router.get("", response_model=list[PersonOut])
def list_people():
    """List all people ordered by name."""
    with get_db() as db:
        rows = db.execute("SELECT id, name, role, created_at FROM people ORDER BY name").fetchall()
    return [PersonOut(**dict(r)) for r in rows]


@router.post("", response_model=PersonOut, status_code=201)
def create_person(req: PersonCreate):
    """Create a new person with a validated role."""
    if req.role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{req.role}'. Must be one of: {', '.join(VALID_ROLES)}",
        )
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO people (name, role) VALUES (?, ?)",
            (req.name, req.role),
        )
        person_id = cursor.lastrowid
        row = db.execute("SELECT id, name, role, created_at FROM people WHERE id = ?", (person_id,)).fetchone()
        log_event(db, "person", person_id, req.name, "created", f"role={req.role}")
    return PersonOut(**dict(row))


@router.delete("/{person_id}")
def delete_person(person_id: int):
    """Delete a person by ID."""
    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        db.execute("DELETE FROM people WHERE id = ?", (person_id,))
        log_event(db, "person", person_id, row["name"], "deleted")
    return {"status": "deleted", "id": person_id}
