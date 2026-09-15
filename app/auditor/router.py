import hmac
import json
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.database import get_db
from . import config, engine, store

router = APIRouter(prefix="/api/auditor", tags=["data-auditor"])


def authorized(request):
    try:
        try:
            loopback = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            loopback = False
        if request.url.scheme != "https" and not loopback:
            return False
        expected = "Bearer " + config.settings().operator_token
        origin = request.headers.get("origin")
        own_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin.rstrip("/") != own_origin:
            return False
        return hmac.compare_digest(request.headers.get("authorization", "").encode(), expected.encode())
    except ValueError:
        return False


def operator(request: Request):
    if not authorized(request):
        raise HTTPException(401, "Audit controls require the operator key and HTTPS or a loopback connection.")


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    flow_ids: list[int] = Field(max_length=100)
    overnight: bool
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

    @model_validator(mode="after")
    def scope(self):
        if any(type(value) is not int or value <= 0 for value in self.flow_ids) or len(set(self.flow_ids)) != len(self.flow_ids):
            raise ValueError("Flow selection is invalid.")
        if self.enabled and not self.flow_ids:
            raise ValueError("Select at least one flow.")
        return self


@router.get("/status")
def status(request: Request):
    ready = config.readiness()
    access = authorized(request)
    return {**ready, "authorized": access, **(store.projection() if access else {})}


@router.get("/catalog", dependencies=[Depends(operator)])
async def catalog():
    try:
        approved = await engine.read_catalog()
        ids = {d["flow_id"] for d in approved["datasets"]}
        with get_db() as db:
            flows = [dict(row) for row in db.execute("""SELECT f.id,f.name,g.name AS group_name FROM flows f
                LEFT JOIN flow_group_members m ON m.flow_id=f.id
                LEFT JOIN flow_groups g ON g.id=m.group_id ORDER BY g.name,f.name""") if row["id"] in ids]
        return {"flows": flows}
    except Exception:
        raise HTTPException(503, "The restricted reader is unavailable. Restore access and try again.") from None


@router.put("/settings", dependencies=[Depends(operator)])
async def save_settings(body: SettingsUpdate):
    if body.enabled:
        try:
            approved = await engine.read_catalog()
            ids = {d["flow_id"] for d in approved["datasets"]}
            if not set(body.flow_ids).issubset(ids):
                raise HTTPException(422, "A selected flow is no longer approved for auditing.")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "The restricted reader is unavailable. Your selections have not changed.") from None
    else:
        engine.cancel()
    return store.write_settings(body.model_dump())


@router.post("/run", dependencies=[Depends(operator)])
async def run_now():
    try:
        await engine.read_catalog()
        return {"id": engine.start()}
    except ValueError as exc:
        # start() uses static public errors; transport uses static reason codes.
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "The restricted reader is unavailable. No audit started.") from None


@router.post("/stop", dependencies=[Depends(operator)])
def stop():
    engine.cancel()
    return {"status": "stopping"}


@router.get("/findings/{finding_id}", dependencies=[Depends(operator)])
def evidence(finding_id: int):
    with get_db() as db:
        row = db.execute("SELECT message,evidence,alert_id FROM auditor_findings WHERE id=?", (finding_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Finding not found.")
    return {"message": row["message"], "evidence": json.loads(row["evidence"]), "alert_id": row["alert_id"]}
