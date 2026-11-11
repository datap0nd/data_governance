"""What the service logs hold, and the manual clean-up."""
from fastapi import APIRouter, HTTPException, Request

from app import log_housekeeping, logging_setup
from app.database import get_db
from app.routers.eventlog import get_actor, log_event
from app.routers.people import require_host

router = APIRouter(prefix="/api/system/logs", tags=["system"])


def _state() -> dict:
    return {
        **log_housekeeping.summary(),
        "keep_days": log_housekeeping.keep_days(),
        "max_total_mb": log_housekeeping.max_total_mb(),
        "request_log": logging_setup.request_log_mode(),
    }


@router.get("")
def get_logs():
    return _state()


@router.post("/prune")
def prune_logs(request: Request):
    """Apply the saved limits now instead of waiting for the next timed run."""
    require_host(request, "Only the host PC can clean up logs.")
    result = log_housekeeping.run()
    if result is None:
        raise HTTPException(409, "A log clean-up is already running, or it failed. Check the service log.")
    with get_db() as db:
        log_event(
            db, "system", None, "Logs", "pruned",
            f"deleted {result['deleted_files']} file(s), {result['deleted_bytes']} bytes", get_actor(request),
        )
    return {**_state(), "deleted_files": result["deleted_files"], "deleted_bytes": result["deleted_bytes"],
            "skipped": result["skipped"], "over_limit": result["over_limit"]}
