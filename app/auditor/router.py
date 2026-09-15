import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_db
from app.local_access import is_local_request
from . import config, engine, store

router = APIRouter(prefix="/api/auditor", tags=["data-auditor"])


def local_operator(request: Request):
    if not is_local_request(request):
        raise HTTPException(403, "Auditor controls and evidence are available only on this Metronome computer.")


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    paused: bool
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


@router.get("/status")
def status(request: Request):
    ready = config.readiness()
    access = is_local_request(request)
    projection = store.projection() if access else {}
    latest = projection.get("latest")
    if latest and latest.get("reason") == "inspection_unavailable":
        ready["reader"] = {"available": False,
                           "detail": "The restricted reader was unavailable during the latest audit."}
    model_state = (latest or {}).get("coverage", {}).get("model")
    if model_state and model_state not in {"pending", "completed", "not_run_no_evidence"}:
        ready["model"] = {"available": False,
                          "detail": "Local AI review was unavailable or rejected during the latest audit."}
    return {**ready, "authorized": access, **projection}


@router.get("/catalog", dependencies=[Depends(local_operator)])
async def catalog():
    with get_db() as db:
        flows = [dict(row) for row in db.execute("""SELECT f.id,f.name,g.name AS group_name
            FROM flows f LEFT JOIN flow_group_members m ON m.flow_id=f.id
            LEFT JOIN flow_groups g ON g.id=m.group_id ORDER BY g.name,f.name""")]
    return {"flows": flows}


@router.put("/settings", dependencies=[Depends(local_operator)])
def save_settings(body: SettingsUpdate):
    if body.paused:
        engine.cancel("paused")
    return store.write_settings(body.model_dump())


@router.post("/run", dependencies=[Depends(local_operator)])
async def run_now():
    try:
        cfg = config.settings()
        store.pin_model(cfg)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        await engine.read_catalog(cfg)
    except Exception:
        raise HTTPException(503, "The restricted reader is unavailable. No audit started.") from None
    try:
        return {"id": engine.start()}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/stop", dependencies=[Depends(local_operator)])
def stop():
    engine.cancel("stopped")
    return {"status": "stopping"}


@router.get("/findings/{finding_id}", dependencies=[Depends(local_operator)])
def evidence(finding_id: int):
    with get_db() as db:
        row = db.execute("SELECT message,evidence,alert_id FROM auditor_findings WHERE id=?", (finding_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Finding not found.")
    return {"message": row["message"], "evidence": json.loads(row["evidence"]),
            "alert_id": row["alert_id"]}
