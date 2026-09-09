"""User profiles - BI and Business roles with optional SQL identities."""

from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import PersonOut, PersonCreate, PersonUpdate

router = APIRouter(prefix="/api/people", tags=["people"])

VALID_ROLES = ["BI", "Business"]
PERSON_COLUMNS = "id, name, role, email, sql_username, created_at"


def _sql_username(value: str | None) -> str | None:
    """Normalize profile metadata without interpreting it as SQL."""
    value = (value or "").strip()
    if not value:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise HTTPException(status_code=422, detail="SQL user cannot contain control characters")
    # PostgreSQL identifiers are limited by bytes, including quoted names.
    if len(value.encode("utf-8")) > 63:
        raise HTTPException(status_code=422, detail="SQL user must be 63 UTF-8 bytes or fewer")
    return value


@router.get("/roles")
def get_roles():
    """Return the list of valid roles."""
    return VALID_ROLES


@router.get("", response_model=list[PersonOut])
def list_people():
    """List all people ordered by name."""
    with get_db() as db:
        rows = db.execute(f"SELECT {PERSON_COLUMNS} FROM people ORDER BY name").fetchall()
    return [PersonOut(**dict(r)) for r in rows]


@router.post("", response_model=PersonOut, status_code=201)
def create_person(req: PersonCreate, request: Request):
    """Create a new person with a validated role."""
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    if req.role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{req.role}'. Must be one of: {', '.join(VALID_ROLES)}",
        )
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO people (name, role, email, sql_username) VALUES (?, ?, ?, ?)",
            (req.name.strip(), req.role, (req.email or "").strip() or None, _sql_username(req.sql_username)),
        )
        person_id = cursor.lastrowid
        row = db.execute(f"SELECT {PERSON_COLUMNS} FROM people WHERE id = ?", (person_id,)).fetchone()
        log_event(db, "person", person_id, req.name, "created", f"role={req.role}", get_actor(request))
    return PersonOut(**dict(row))


@router.patch("/{person_id}", response_model=PersonOut)
def update_person(person_id: int, req: PersonUpdate, request: Request):
    """Update a profile; future Flow SQL jobs freeze its ownership identity."""
    data = req.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No changes provided")
    if "role" in data and data["role"] not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{data['role']}'. Must be one of: {', '.join(VALID_ROLES)}",
        )

    with get_db() as db:
        existing = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Person not found")

        fields = []
        values = []
        for field_name in ("name", "role", "email", "sql_username"):
            if field_name not in data:
                continue
            value = data[field_name]
            if isinstance(value, str):
                value = value.strip()
            if field_name == "email" and not value:
                value = None
            if field_name == "sql_username":
                value = _sql_username(value)
            if field_name == "name" and not value:
                raise HTTPException(status_code=400, detail="Name cannot be empty")
            fields.append(f"{field_name} = ?")
            values.append(value)

        if not fields:
            raise HTTPException(status_code=400, detail="No changes provided")
        values.append(person_id)
        db.execute(f"UPDATE people SET {', '.join(fields)} WHERE id = ?", values)
        row = db.execute(f"SELECT {PERSON_COLUMNS} FROM people WHERE id = ?", (person_id,)).fetchone()
        log_event(db, "person", person_id, row["name"], "updated", ", ".join(data.keys()), get_actor(request))
    return PersonOut(**dict(row))


@router.delete("/{person_id}")
def delete_person(person_id: int, request: Request):
    """Delete a person by ID."""
    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        db.execute("DELETE FROM people WHERE id = ?", (person_id,))
        log_event(db, "person", person_id, row["name"], "deleted", actor=get_actor(request))
    return {"status": "deleted", "id": person_id}
