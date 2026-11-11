"""The Settings page: everyone may read it, only the host PC may change it."""
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import settings_registry, user_identity
from app.database import get_db
from app.local_access import require_host
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/system/settings", tags=["system"])


class SettingsWrite(BaseModel):
    # A value of null returns that setting to its default.
    values: dict[str, Any] = Field(min_length=1, max_length=200)


def _state(request: Request) -> dict:
    _person_id, is_host = user_identity.request_identity(request)
    return {"can_edit": bool(is_host), "sections": settings_registry.describe()}


@router.get("")
def get_settings(request: Request):
    return _state(request)


@router.put("")
def save_settings(body: SettingsWrite, request: Request):
    require_host(request, "Only the host PC can change settings.")
    try:
        changed = settings_registry.save(body.values)
    except settings_registry.SettingsRejected as exc:
        # Nothing was saved; each message belongs beside its own field.
        return JSONResponse(status_code=422, content={
            "detail": "Nothing was saved. Fix the highlighted settings and save again.",
            "errors": exc.errors,
        })
    except Exception as exc:
        raise HTTPException(
            503, f"Saving stopped part-way ({type(exc).__name__}). Reload the page to see what was saved, then try again."
        ) from exc
    if changed:
        with get_db() as db:
            for record in changed:
                log_event(
                    db, "system", None, "Settings", "setting_changed",
                    f"{record['label']}: {settings_registry.display(record['old'])} to "
                    f"{settings_registry.display(record['new'])}",
                    get_actor(request),
                )
    return {**_state(request), "changed": [record["key"] for record in changed]}
