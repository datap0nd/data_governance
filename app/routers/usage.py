from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.database import get_db
from app.local_access import is_server_machine
from app.usage import PREMIUM_VIEW_WEIGHT, sync_usage_from_csv

router = APIRouter(prefix="/api/usage", tags=["usage"])


class PremiumViewersUpdate(BaseModel):
    emails: list[str] = []


def _require_local(request: Request):
    ip = request.client.host if request.client else ""
    if not is_server_machine(ip):
        raise HTTPException(status_code=403, detail="Usage admin is restricted to the server machine")


def _clean_email(value: str) -> str | None:
    clean = (value or "").strip().lower()
    if not clean:
        return None
    if len(clean) > 320 or "@" not in clean:
        return None
    return clean


@router.get("/premium-viewers")
def list_premium_viewers(request: Request):
    _require_local(request)
    with get_db() as db:
        rows = db.execute("SELECT email, created_at FROM premium_viewers ORDER BY email").fetchall()
    return {
        "weight": PREMIUM_VIEW_WEIGHT,
        "viewers": [dict(r) for r in rows],
    }


@router.put("/premium-viewers")
def update_premium_viewers(body: PremiumViewersUpdate, request: Request):
    _require_local(request)
    cleaned = []
    seen = set()
    for value in body.emails:
        email = _clean_email(value)
        if not email or email in seen:
            continue
        seen.add(email)
        cleaned.append(email)

    with get_db() as db:
        db.execute("DELETE FROM premium_viewers")
        for email in cleaned:
            db.execute("INSERT INTO premium_viewers (email) VALUES (?)", (email,))

    return {"status": "saved", "count": len(cleaned), "weight": PREMIUM_VIEW_WEIGHT}


@router.post("/sync")
def sync_usage(request: Request):
    _require_local(request)
    with get_db() as db:
        return sync_usage_from_csv(db, force=True)

