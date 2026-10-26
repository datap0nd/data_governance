"""User profiles - BI and Business roles with optional SQL identities.

Everyone can read Users; only the host PC changes them, because a person's
profile decides who signs in as whom, whose SQL user a Flow writes with and
where Flow notifications go. The one exception: each person can set or clear
their own SQL password.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app import sql_passwords, user_identity
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import PersonOut, PersonCreate, PersonUpdate, SqlPasswordWrite

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


def require_host(request: Request, detail: str = "Only the host PC can change Users.") -> None:
    """Refuse a change that only the machine running Metronome may make."""
    _person_id, is_host = user_identity.request_identity(request)
    if not is_host:
        raise HTTPException(status_code=403, detail=detail)


def require_host_or_self(request: Request, person_id: int, detail: str) -> None:
    """Refuse a change unless it comes from the host PC or that person's own computer."""
    signed_in_as, is_host = user_identity.request_identity(request)
    if not is_host and signed_in_as != person_id:
        raise HTTPException(status_code=403, detail=detail)


def _out(row, saved_passwords: set[int], signed_in: dict[int, int]) -> PersonOut:
    return PersonOut(
        **dict(row),
        sql_password_set=row["id"] in saved_passwords,
        signed_in_computers=signed_in.get(row["id"], 0),
    )


def _one(db, person_id: int) -> PersonOut:
    row = db.execute(f"SELECT {PERSON_COLUMNS} FROM people WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    return _out(row, sql_passwords.saved_ids(), user_identity.signed_in_counts(db))


def _store_password(person_id: int, password: str | None) -> None:
    try:
        sql_passwords.store(person_id, password)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/roles")
def get_roles():
    """Return the list of valid roles."""
    return VALID_ROLES


@router.get("", response_model=list[PersonOut])
def list_people():
    """List all people ordered by name."""
    with get_db() as db:
        rows = db.execute(f"SELECT {PERSON_COLUMNS} FROM people ORDER BY name").fetchall()
        signed_in = user_identity.signed_in_counts(db)
    saved = sql_passwords.saved_ids()
    return [_out(r, saved, signed_in) for r in rows]


@router.post("", response_model=PersonOut, status_code=201)
def create_person(req: PersonCreate, request: Request):
    """Create a new person with a validated role."""
    require_host(request)
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
        log_event(db, "person", person_id, req.name, "created", f"role={req.role}", get_actor(request))
        person = _one(db, person_id)
    return person


@router.patch("/{person_id}", response_model=PersonOut)
def update_person(person_id: int, req: PersonUpdate, request: Request):
    """Update a profile; future Flow SQL jobs freeze its ownership identity.

    A different SQL username clears the saved SQL password, which belonged to
    the previous SQL user.
    """
    require_host(request)
    data = req.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No changes provided")
    if "role" in data and data["role"] not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{data['role']}'. Must be one of: {', '.join(VALID_ROLES)}",
        )

    with get_db() as db:
        existing = db.execute("SELECT id, name, sql_username FROM people WHERE id = ?", (person_id,)).fetchone()
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
        sql_user_changed = (
            "sql_username" in data and _sql_username(data["sql_username"]) != existing["sql_username"]
        )
        if sql_user_changed and person_id in sql_passwords.saved_ids():
            # Before the profile changes, so a failed write leaves both as they were.
            _store_password(person_id, None)
        values.append(person_id)
        db.execute(f"UPDATE people SET {', '.join(fields)} WHERE id = ?", values)
        row = db.execute(f"SELECT {PERSON_COLUMNS} FROM people WHERE id = ?", (person_id,)).fetchone()
        log_event(db, "person", person_id, row["name"], "updated", ", ".join(data.keys()), get_actor(request))
        person = _one(db, person_id)
    user_identity.clear_cache()
    return person


@router.put("/{person_id}/sql-password", response_model=PersonOut)
def set_sql_password(person_id: int, body: SqlPasswordWrite, request: Request):
    """Save a person's SQL password in the host PC's settings file.

    The host PC sets anyone's; a signed-in person sets their own.
    """
    require_host_or_self(request, person_id, "Only the host PC or that person can set their SQL password.")
    if any(ord(char) < 32 or ord(char) == 127 for char in body.password):
        raise HTTPException(status_code=422, detail="SQL password cannot contain line breaks or control characters.")
    with get_db() as db:
        row = db.execute("SELECT id, name, sql_username FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        if not row["sql_username"]:
            raise HTTPException(status_code=409, detail="Add this person's SQL username before saving a SQL password.")
        _store_password(person_id, body.password)
        log_event(db, "person", person_id, row["name"], "sql_password_set", None, get_actor(request))
        return _one(db, person_id)


@router.delete("/{person_id}/sql-password", response_model=PersonOut)
def clear_sql_password(person_id: int, request: Request):
    """Remove a person's SQL password from the settings file (host PC or that person)."""
    require_host_or_self(request, person_id, "Only the host PC or that person can clear their SQL password.")
    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        _store_password(person_id, None)
        log_event(db, "person", person_id, row["name"], "sql_password_cleared", None, get_actor(request))
        return _one(db, person_id)


@router.delete("/{person_id}/sign-ins", response_model=PersonOut)
def sign_out_person(person_id: int, request: Request):
    """Sign every computer out of one person; each then chooses again."""
    require_host(request, "Only the host PC can sign computers out.")
    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        removed = user_identity.sign_out_person(db, person_id)
        log_event(db, "person", person_id, row["name"], "signed_out", f"bindings={removed}", get_actor(request))
        return _one(db, person_id)


@router.delete("/{person_id}")
def delete_person(person_id: int, request: Request):
    """Delete a person by ID, with their sign-ins and saved SQL password."""
    require_host(request)
    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        db.execute("DELETE FROM people WHERE id = ?", (person_id,))
        user_identity.sign_out_person(db, person_id)
        log_event(db, "person", person_id, row["name"], "deleted", actor=get_actor(request))
    if person_id in sql_passwords.saved_ids():
        try:
            sql_passwords.store(person_id, None)
        except (RuntimeError, ValueError, OSError):
            # Ids are never reused, so a leftover setting can't sign anyone in.
            logging.getLogger(__name__).warning("Could not remove the SQL password of deleted person %s", person_id)
    user_identity.clear_cache()
    return {"status": "deleted", "id": person_id}
