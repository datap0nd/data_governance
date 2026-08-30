import logging
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import fastapi
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.config import TMDL_ROOT, DB_PATH, PGHOST, PGDATABASE, PGUSER
from app.database import get_db
from app.local_access import require_app_access
from app.scanner.control import ScannerWorkCancelled, assert_not_cancelled
from app.scanner.control import current_cancel_generation
from app.scanner import jobs as scanner_jobs
from app.scanner.runner import run_scan
from app.scanner.prober import run_probe
from app.scanner import pbi_auth
from app.scanner.pbi_sync import (
    _record_sync_run,
    get_pending_pbi_sync,
    latest_pbi_sync,
    latest_successful_pbi_sync,
    pbi_auth_mode,
    pbi_sync_freshness,
    rdp_console_guard_status,
    stop_pbi_sync_processes,
    trigger_pbi_sync,
    trigger_pbi_sync_and_wait,
    import_pbi_data,
    import_pbi_usage_data,
    trigger_pbi_usage_sync,
)
from app.scanner.walker import diagnose_reports_root
from app.models import ScanRunOut
from app.scanner.lifecycle import parse_components, redact_component_payload
from app.usage import sync_usage_from_csv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])

_job_executor: ThreadPoolExecutor | None = None
_job_futures: dict[int, Future] = {}
_job_future_lock = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _job_executor
    with _job_future_lock:
        if _job_executor is None:
            # Scanner mutations are deliberately serialized. A focused lineage
            # recheck and a full scan must never reconcile the catalog at once.
            _job_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="scanner-operation"
            )
        return _job_executor


def _submit_job(job_id: int, worker, *args) -> None:
    def guarded_worker():
        try:
            result = worker(int(job_id), *args)
        except Exception as exc:
            # This is a last-resort durability boundary. Individual workers
            # retain their richer failure messages, but a crash before their
            # try/except (for example while marking the row running) must not
            # leave Scanner permanently blocked by a phantom active job.
            scanner_jobs.finish_job(
                job_id,
                status="failed",
                result={
                    "status": "failed",
                    "error_type": type(exc).__name__,
                },
                message="Scanner worker crashed before it recorded a terminal result.",
            )
            raise
        current = scanner_jobs.get_job(job_id)
        if current is not None and current.get("active"):
            scanner_jobs.finish_job(
                job_id,
                status="failed",
                result={"status": "failed", "error_type": "IncompleteWorkerExit"},
                message="Scanner worker exited without recording a terminal result.",
            )
        return result

    try:
        future = _executor().submit(guarded_worker)
    except Exception as exc:
        scanner_jobs.finish_job(
            job_id,
            status="failed",
            result={"status": "failed", "error_type": type(exc).__name__},
            message="Scanner worker could not be started.",
        )
        raise
    with _job_future_lock:
        _job_futures[int(job_id)] = future

    def forget(done: Future) -> None:
        try:
            done.result()
        except Exception:
            logger.exception("Scanner background job %s crashed", job_id)
        finally:
            with _job_future_lock:
                _job_futures.pop(int(job_id), None)

    future.add_done_callback(forget)


def _cancel_queued_jobs() -> None:
    # Future.cancel() may invoke callbacks synchronously. Copy under the lock,
    # then cancel outside it so the callback can safely remove its registry row.
    with _job_future_lock:
        futures = list(_job_futures.values())
    for future in futures:
        future.cancel()


def shutdown_scanner_executor() -> None:
    global _job_executor
    _cancel_queued_jobs()
    with _job_future_lock:
        executor = _job_executor
        _job_executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _execute_full_scan_job(
    job_id: int,
    generation: int | None,
    stop_result: dict,
    pbi_source: str = "manual_scanner_refresh",
    launch_usage_sync: bool = False,
) -> dict:
    scanner_jobs.mark_running(
        job_id,
        current_step="Syncing Power BI metadata",
        message="Refreshing report metadata before local report discovery.",
    )
    try:
        pbi_result = trigger_pbi_sync_and_wait(
            pbi_source,
            cancel_existing=False,
            cancel_generation=generation,
            operation_id=job_id,
        )
        pbi_status = (pbi_result.get("status") or "").lower()
        if pbi_status not in {"completed", "skipped"}:
            terminal_status = "stopped" if pbi_status in {"stopped", "cancelled", "canceled"} else "failed"
            scanner_jobs.finish_job(
                job_id,
                status=terminal_status,
                result={
                    "status": "pbi_sync_not_completed",
                    "pbi_sync": pbi_result,
                    "stop": stop_result,
                },
                message=(
                    "Full scan stopped before report discovery because the "
                    f"Power BI sync was {pbi_result.get('status') or 'not completed'}."
                ),
            )
            return scanner_jobs.get_job(job_id) or {
                "status": terminal_status,
                "pbi_sync": pbi_result,
            }
        scanner_jobs.heartbeat(
            job_id,
            current_step="Starting local report scan",
            message="Power BI metadata sync finished; starting PBIX/TMDL discovery.",
        )
        scan_result = run_scan(
            cancel_generation=generation,
            run_followup_probe=True,
            operation_id=job_id,
        )
        if launch_usage_sync and (scan_result.get("status") or "").lower() in {
            "completed",
            "completed_with_warnings",
        }:
            try:
                trigger_pbi_usage_sync(
                    cancel_existing=False,
                    cancel_generation=generation,
                )
            except Exception:
                logger.exception("Scheduled Power BI usage sync failed to start")
        return scan_result
    except ScannerWorkCancelled as exc:
        scanner_jobs.finish_job(
            job_id,
            status="stopped",
            result={"status": "stopped", "message": str(exc)},
            message=str(exc),
        )
        return {"status": "stopped", "message": str(exc)}
    except Exception as exc:
        logger.exception("Background full scan failed")
        scanner_jobs.finish_job(
            job_id,
            status="failed",
            result={"status": "failed", "error": str(exc)},
            message="Full scan failed; review server logs.",
        )
        return redact_component_payload({"status": "failed", "error": str(exc)})


def _execute_probe_job(job_id: int, generation: int | None) -> None:
    scanner_jobs.mark_running(
        job_id,
        current_step="Probing source freshness",
        message="Checking file and PostgreSQL sources.",
    )
    try:
        result = redact_component_payload(
            run_probe(cancel_generation=generation, operation_id=job_id)
        )
        scanner_jobs.finish_job(job_id, status=result.get("status") or "completed", result=result)
    except ScannerWorkCancelled as exc:
        scanner_jobs.finish_job(
            job_id,
            status="stopped",
            result={"status": "stopped", "message": str(exc)},
            message=str(exc),
        )
    except Exception as exc:
        logger.exception("Background source probe failed")
        scanner_jobs.finish_job(
            job_id,
            status="failed",
            result={"status": "failed", "error": str(exc)},
            message="Source probe failed; review server logs.",
        )


def _execute_scan_only_job(job_id: int, generation: int | None) -> None:
    """Run report discovery/probing for a pre-reserved durable job."""
    scanner_jobs.mark_running(
        job_id,
        current_step="Starting local report scan",
        message="Starting PBIX/TMDL discovery.",
    )
    try:
        run_scan(
            cancel_generation=generation,
            run_followup_probe=True,
            operation_id=job_id,
        )
    except ScannerWorkCancelled as exc:
        scanner_jobs.finish_job(
            job_id,
            status="stopped",
            result={"status": "stopped", "message": str(exc)},
            message=str(exc),
        )
    except Exception as exc:
        logger.exception("Background report scan failed")
        scanner_jobs.finish_job(
            job_id,
            status="failed",
            result={"status": "failed", "error": str(exc)},
            message="Report scan failed; review server logs.",
        )


def _execute_postgres_cron_job(job_id: int, generation: int | None) -> None:
    from app.scanner.pg_cron import scan_pg_cron

    scanner_jobs.mark_running(
        job_id,
        current_step="Reading PostgreSQL schedules",
        message="Checking pg_cron materialized-view refresh schedules.",
    )
    try:
        assert_not_cancelled(generation, "PostgreSQL schedule scan")
        result = redact_component_payload(scan_pg_cron())
        assert_not_cancelled(generation, "PostgreSQL schedule scan")
        scanner_jobs.finish_job(
            job_id,
            status=result.get("status") or "completed",
            result=result,
        )
    except ScannerWorkCancelled as exc:
        scanner_jobs.finish_job(
            job_id,
            status="stopped",
            result={"status": "stopped", "message": str(exc)},
            message=str(exc),
        )
    except Exception as exc:
        logger.exception("Background PostgreSQL schedule scan failed")
        scanner_jobs.finish_job(
            job_id,
            status="failed",
            result={"status": "failed", "error": str(exc)},
            message="PostgreSQL schedule scan failed; review server logs.",
        )


def _execute_postgres_lineage_job(job_id: int, generation: int | None) -> None:
    from app.scanner.pg_deps import scan_pg_dependencies

    job = scanner_jobs.get_job(job_id) or {}
    context = job.get("context") if isinstance(job.get("context"), dict) else {}
    report_id = context.get("report_id")
    try:
        report_id = int(report_id) if report_id is not None else None
    except (TypeError, ValueError):
        report_id = None
    scanner_jobs.mark_running(
        job_id,
        current_step="Preparing PostgreSQL lineage recheck",
        message=(
            f"Repairing report #{report_id}'s source identity before catalog discovery."
            if report_id is not None
            else "Resolving the databases required by reports and Flow targets."
        ),
    )
    try:
        result = redact_component_payload(
            scan_pg_dependencies(
                report_id=report_id,
                operation_id=job_id,
                cancel_generation=generation,
            )
        )
        scanner_jobs.finish_job(job_id, status=result.get("status") or "completed", result=result)
    except ScannerWorkCancelled as exc:
        scanner_jobs.finish_job(
            job_id,
            status="stopped",
            result={"status": "stopped", "message": str(exc)},
            message=str(exc),
        )
    except Exception as exc:
        logger.exception("Background PostgreSQL lineage recheck failed")
        scanner_jobs.finish_job(
            job_id,
            status="failed",
            result={"status": "failed", "error": str(exc)},
            message="PostgreSQL lineage recheck failed; review server logs.",
        )


def _require_scan_access(request: Request):
    """Compatibility hook for scan actions that used to require elevated access."""
    require_app_access(request)


@router.post("/run")
def do_scan(request: Request):
    """Compatibility alias for the durable, non-blocking full-scan job."""
    return start_full_scan_job(request)


@router.post("/probe")
def do_probe(request: Request):
    """Compatibility alias for the durable, non-blocking source-probe job."""
    return start_probe_job(request)


def _job_start_response(job: dict, *, accepted: bool, reused: bool, message: str) -> dict:
    return {
        "accepted": accepted,
        "reused": reused,
        "message": message,
        "job": job,
        "job_id": job["id"],
        "status": job["status"],
    }


@router.get("/jobs")
def list_scanner_jobs(limit: int = 30):
    """Return authoritative current/recent scanner work with stale health."""
    return scanner_jobs.list_jobs(limit=limit)


@router.get("/jobs/{job_id}")
def get_scanner_job(job_id: int):
    job = scanner_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scanner job not found")
    return job


@router.post("/jobs/full-scan")
def start_full_scan_job(request: Request):
    """Start a durable full scan without holding the browser request open."""
    _require_scan_access(request)
    job, created = scanner_jobs.reserve_job(
        "full_scan",
        trigger_source="manual",
        current_step="Queued",
        message="Full scan accepted and waiting for its worker.",
        context={"includes_pbi_sync": True},
    )
    if not created:
        same_job = job["job_type"] == "full_scan"
        return _job_start_response(
            job,
            accepted=False,
            reused=same_job,
            message=(
                "This full scan is already running."
                if same_job
                else "Another scanner operation is active; wait for it or stop it first."
            ),
        )

    job_id = int(job["id"])
    stop_result = stop_pbi_sync_processes(
        "New scanner refresh started.", exclude_scanner_job_id=job_id
    )
    _cancel_queued_jobs()
    generation = (stop_result.get("scanner") or {}).get("generation")
    _submit_job(job_id, _execute_full_scan_job, generation, stop_result)
    return _job_start_response(
        scanner_jobs.get_job(job_id),
        accepted=True,
        reused=False,
        message="Full scan started. Progress is available on the Scanner page.",
    )


def start_scheduled_full_scan_job() -> dict:
    """Reserve and submit the daily refresh through the same global lane."""
    job, created = scanner_jobs.reserve_job(
        "full_scan",
        trigger_source="scheduled",
        current_step="Queued",
        message="Scheduled full scan accepted and waiting for its worker.",
        context={"includes_pbi_sync": True, "includes_usage_sync": True},
    )
    if not created:
        return _job_start_response(
            job,
            accepted=False,
            reused=job["job_type"] == "full_scan",
            message="Scheduled scan skipped because scanner work is already active.",
        )

    job_id = int(job["id"])
    stop_result = stop_pbi_sync_processes(
        "New scheduled overall refresh started.",
        exclude_scanner_job_id=job_id,
    )
    _cancel_queued_jobs()
    generation = (stop_result.get("scanner") or {}).get("generation")
    _submit_job(
        job_id,
        _execute_full_scan_job,
        generation,
        stop_result,
        "scheduled_overall_refresh",
        True,
    )
    return _job_start_response(
        scanner_jobs.get_job(job_id),
        accepted=True,
        reused=False,
        message="Scheduled full scan started; live progress is on the Scanner page.",
    )


def start_scheduled_scan_job(
    *, cancel_generation: int | None = None, stop_existing: bool = True
) -> dict:
    """Submit scan-only scheduled work through the durable global lane."""
    job, created = scanner_jobs.reserve_job(
        "full_scan",
        trigger_source="scheduled_scan",
        current_step="Queued",
        message="Scheduled report scan accepted and waiting for its worker.",
        context={"includes_pbi_sync": False},
    )
    if not created:
        return _job_start_response(
            job,
            accepted=False,
            reused=job["job_type"] == "full_scan",
            message="Scheduled report scan skipped because scanner work is already active.",
        )

    job_id = int(job["id"])
    generation = cancel_generation
    if stop_existing:
        stop_result = stop_pbi_sync_processes(
            "New scheduled scan started.",
            exclude_scanner_job_id=job_id,
        )
        _cancel_queued_jobs()
        generation = (stop_result.get("scanner") or {}).get("generation")
    elif generation is None:
        generation = current_cancel_generation()
    _submit_job(job_id, _execute_scan_only_job, generation)
    return _job_start_response(
        scanner_jobs.get_job(job_id),
        accepted=True,
        reused=False,
        message="Scheduled report scan started; live progress is on the Scanner page.",
    )


@router.post("/jobs/probe")
def start_probe_job(request: Request):
    """Start a durable source probe without holding the browser request open."""
    _require_scan_access(request)
    job, created = scanner_jobs.reserve_job(
        "source_probe",
        trigger_source="manual",
        current_step="Queued",
        message="Source probe accepted and waiting for its worker.",
    )
    if not created:
        same_job = job["job_type"] == "source_probe"
        return _job_start_response(
            job,
            accepted=False,
            reused=same_job,
            message=(
                "This source probe is already running."
                if same_job
                else "Another scanner operation is active; wait for it or stop it first."
            ),
        )

    job_id = int(job["id"])
    stop_result = stop_pbi_sync_processes(
        "New source probe started.", exclude_scanner_job_id=job_id
    )
    _cancel_queued_jobs()
    generation = (stop_result.get("scanner") or {}).get("generation")
    _submit_job(job_id, _execute_probe_job, generation)
    return _job_start_response(
        scanner_jobs.get_job(job_id),
        accepted=True,
        reused=False,
        message="Source probe started. Progress is available on the Scanner page.",
    )


@router.post("/jobs/postgres-lineage")
def start_postgres_lineage_job(request: Request, report_id: int | None = None):
    """Start/reuse a durable focused PostgreSQL lineage recheck."""
    _require_scan_access(request)
    job, created = scanner_jobs.reserve_job(
        "postgres_lineage",
        trigger_source="pipeline_recheck",
        current_step="Queued",
        message="PostgreSQL lineage recheck accepted and waiting for its worker.",
        context={"report_id": report_id} if report_id is not None else {},
    )
    if not created:
        existing_context = (
            job.get("context") if isinstance(job.get("context"), dict) else {}
        )
        existing_report_id = existing_context.get("report_id")
        try:
            existing_report_id = (
                int(existing_report_id) if existing_report_id is not None else None
            )
        except (TypeError, ValueError):
            existing_report_id = None
        requested_report_id = int(report_id) if report_id is not None else None
        compatible = job["job_type"] == "full_scan" or (
            job["job_type"] == "postgres_lineage"
            and existing_report_id == requested_report_id
        )
        return _job_start_response(
            job,
            accepted=False,
            reused=compatible,
            message=(
                "Existing full scan will refresh PostgreSQL lineage."
                if job["job_type"] == "full_scan"
                else "This report's PostgreSQL lineage recheck is already running."
                if compatible
                else (
                    "A PostgreSQL lineage recheck for another report is active. "
                    "Wait for it to finish, then recheck this report."
                )
                if job["job_type"] == "postgres_lineage"
                else "A source probe is active; wait for it or stop it before rechecking lineage."
            ),
        )

    job_id = int(job["id"])
    _submit_job(job_id, _execute_postgres_lineage_job, current_cancel_generation())
    return _job_start_response(
        scanner_jobs.get_job(job_id),
        accepted=True,
        reused=False,
        message="PostgreSQL lineage recheck started. Progress is available on the Scanner page.",
    )


@router.post("/jobs/postgres-schedules")
def start_postgres_schedule_job(request: Request):
    """Start/reuse a durable pg_cron schedule discovery job."""
    _require_scan_access(request)
    job, created = scanner_jobs.reserve_job(
        "postgres_schedules",
        trigger_source="manual",
        current_step="Queued",
        message="PostgreSQL schedule scan accepted and waiting for its worker.",
    )
    if not created:
        return _job_start_response(
            job,
            accepted=False,
            reused=job["job_type"] == "postgres_schedules",
            message="Another scanner operation is active; wait for it or stop it first.",
        )
    job_id = int(job["id"])
    _submit_job(job_id, _execute_postgres_cron_job, current_cancel_generation())
    return _job_start_response(
        scanner_jobs.get_job(job_id),
        accepted=True,
        reused=False,
        message="PostgreSQL schedule scan started. Progress is on the Scanner page.",
    )


@router.get("/probe/runs")
def list_probe_runs():
    """List all probe runs, most recent first."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM probe_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/pbi-sync")
def do_pbi_sync(request: Request):
    """Launch PBI sync in the user's interactive session."""
    _require_scan_access(request)
    return trigger_pbi_sync()


@router.post("/pbi-sync/stop")
def stop_pbi_sync(request: Request):
    """Stop running scanner refresh work and PBI helper processes."""
    _require_scan_access(request)
    result = stop_pbi_sync_processes()
    _cancel_queued_jobs()
    return result


@router.get("/pbi-sync/status")
def pbi_sync_status():
    """Return latest PBI sync status and freshness."""
    try:
        auth = pbi_auth.auth_status()
    except Exception as exc:
        auth = {"connected": False, "message": f"Could not read Power BI auth status: {exc}"}
    return {
        "auth_mode": pbi_auth_mode(),
        "auth": auth,
        "refresh": {
            "latest_attempt": latest_pbi_sync("refresh"),
            "latest_success": latest_successful_pbi_sync("refresh"),
            "freshness": pbi_sync_freshness(),
        },
        "usage": {
            "latest_attempt": latest_pbi_sync("usage"),
            "latest_success": latest_successful_pbi_sync("usage"),
        },
        "pending": get_pending_pbi_sync(),
        "rdp_guard": rdp_console_guard_status(),
    }


@router.get("/pbi-auth/status")
def get_pbi_auth_status():
    """Return saved Microsoft account sign-in state for headless PBI sync."""
    return pbi_auth.auth_status()


@router.post("/pbi-auth/connect")
def start_pbi_auth(request: Request):
    """Start a device-code sign-in; the code can be entered from any device."""
    _require_scan_access(request)
    try:
        return pbi_auth.start_device_flow()
    except Exception as exc:
        logger.exception("Power BI connect failed")
        return {"status": "failed", "message": f"Could not start the Microsoft sign-in: {exc}"}


@router.post("/pbi-auth/disconnect")
def disconnect_pbi_auth(request: Request):
    """Forget the saved Microsoft account sign-in."""
    _require_scan_access(request)
    return pbi_auth.disconnect()


@router.post("/pbi-import")
def do_pbi_import(request: Request, data: dict = fastapi.Body(...)):
    """Receive PBI data from the PS1 script and update the DB."""
    _require_scan_access(request)
    return import_pbi_data(data)


def _scan_run_out(row) -> ScanRunOut:
    data = dict(row)
    data["components"] = parse_components(data.pop("components_json", None))
    return ScanRunOut(**data)


@router.get("/runs", response_model=list[ScanRunOut])
def list_scan_runs():
    """List all scan runs, most recent first."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    return [_scan_run_out(r) for r in rows]


@router.get("/diagnose")
def diagnose_scan():
    """Step-by-step diagnostics of the scanner discovery logic."""
    return diagnose_reports_root(TMDL_ROOT)


@router.get("/runs/{run_id}", response_model=ScanRunOut)
def get_scan_run(run_id: int):
    with get_db() as db:
        r = db.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return {"error": "Scan run not found"}
    return _scan_run_out(r)


@router.post("/pg-deps")
def do_pg_deps(request: Request):
    """Compatibility alias for durable PostgreSQL lineage discovery."""
    return start_postgres_lineage_job(request)


@router.post("/pg-cron")
def do_pg_cron(request: Request):
    """Compatibility alias for durable PostgreSQL schedule discovery."""
    return start_postgres_schedule_job(request)


class OpenPathRequest(BaseModel):
    path: str


class PbiSyncRunStatus(BaseModel):
    sync_type: str = "refresh"
    status: str
    message: str | None = None
    details: dict | None = None


@router.post("/pbi-sync/run-status")
def record_pbi_sync_run_status(body: PbiSyncRunStatus, request: Request):
    """Record status from a PowerShell sync process that failed before import."""
    _require_scan_access(request)
    sync_type = (body.sync_type or "refresh").strip().lower()
    if sync_type not in {"refresh", "usage"}:
        raise HTTPException(status_code=400, detail="sync_type must be refresh or usage")
    status = (body.status or "").strip().lower()
    if status not in {"launched", "completed", "failed", "skipped", "stopped"}:
        raise HTTPException(status_code=400, detail="status is not valid")
    _record_sync_run(sync_type, status, body.message, body.details)
    return {"status": "recorded"}


@router.post("/open-path")
def open_path(body: OpenPathRequest, request: Request):
    """Open the containing folder of a file path in the OS file explorer."""
    _require_scan_access(request)
    target = Path(body.path)

    # If it's a file, open its parent folder; if directory, open it directly
    folder = target.parent if target.is_file() else target
    if not folder.exists():
        # Try the path as-is even if we can't verify (network paths)
        folder = target.parent if not target.suffix == "" else target

    try:
        if sys.platform == "win32":
            if target.is_file():
                # Select the file in Explorer
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(folder)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return {"status": "ok", "opened": str(folder)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open path: {e}")


@router.get("/diagnostic")
def diagnostic_report():
    """Generate a comprehensive diagnostic report for debugging."""
    import os
    import platform
    from pathlib import Path
    from app.config import REPORTS_PATH

    report = {}

    # ── Environment ──
    report["environment"] = {
        "python_version": sys.version,
        "platform": platform.platform(),
        "db_path": DB_PATH,
        "db_exists": Path(DB_PATH).exists(),
        "db_size_kb": round(Path(DB_PATH).stat().st_size / 1024) if Path(DB_PATH).exists() else 0,
        "tmdl_root": TMDL_ROOT,
        "tmdl_root_exists": Path(TMDL_ROOT).is_dir() if TMDL_ROOT else False,
        "reports_path": REPORTS_PATH,
        "pghost": PGHOST or "(not set)",
        "pgdatabase": PGDATABASE or "(not set)",
        "pguser": PGUSER or "(not set)",
    }

    with get_db() as db:
        # ── Table Row Counts ──
        tables = [
            "sources", "reports", "report_tables", "report_pages",
            "report_visuals", "visual_fields", "report_measures", "report_columns",
            "source_probes", "probe_runs", "scan_runs",
            "source_dependencies", "alerts", "actions", "checks",
            "upstream_systems", "tasks", "event_log", "people",
        ]
        counts = {}
        for t in tables:
            try:
                row = db.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()
                counts[t] = row["c"]
            except Exception:
                counts[t] = "TABLE_NOT_FOUND"
        report["row_counts"] = counts

        # ── Sources Summary ──
        src_rows = db.execute("""
            SELECT s.id, s.name, s.type, s.discovered_by,
                   sp.status AS probe_status,
                   (SELECT COUNT(*) FROM report_tables rt WHERE rt.source_id = s.id) AS report_count,
                   (SELECT COUNT(*) FROM source_dependencies sd WHERE sd.source_id = s.id) AS dep_from_count,
                   (SELECT COUNT(*) FROM source_dependencies sd WHERE sd.depends_on_id = s.id) AS dep_to_count
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE s.archived = 0
            ORDER BY s.name
        """).fetchall()
        report["sources"] = [
            {
                "id": r["id"], "name": r["name"], "type": r["type"],
                "discovered_by": r["discovered_by"], "probe_status": r["probe_status"] or "unknown",
                "report_count": r["report_count"],
                "dep_from_count": r["dep_from_count"], "dep_to_count": r["dep_to_count"],
            }
            for r in src_rows
        ]

        # ── Source Name Issues ──
        # Sources with IP prefix still in name
        ip_sources = db.execute(
            "SELECT id, name FROM sources WHERE name LIKE '%.%.%.%/%' AND archived = 0"
        ).fetchall()
        report["sources_with_ip_prefix"] = [{"id": r["id"], "name": r["name"]} for r in ip_sources]

        # Potential duplicate source names (same table, different prefix)
        dup_check = db.execute("""
            SELECT s1.id AS id1, s1.name AS name1, s2.id AS id2, s2.name AS name2
            FROM sources s1
            JOIN sources s2 ON s1.id < s2.id
                AND s1.type = s2.type
                AND s1.archived = 0 AND s2.archived = 0
            WHERE (
                s1.name LIKE '%.' || SUBSTR(s2.name, INSTR(s2.name, '.') + 1)
                OR s2.name LIKE '%.' || SUBSTR(s1.name, INSTR(s1.name, '.') + 1)
            )
            LIMIT 50
        """).fetchall()
        report["potential_duplicate_sources"] = [
            {"id1": r["id1"], "name1": r["name1"], "id2": r["id2"], "name2": r["name2"]}
            for r in dup_check
        ]

        # ── Broken FK References ──
        broken_fks = {}

        # report_tables pointing to non-existent sources
        broken = db.execute("""
            SELECT rt.id, rt.report_id, rt.table_name, rt.source_id
            FROM report_tables rt
            WHERE rt.source_id IS NOT NULL
              AND rt.source_id NOT IN (SELECT id FROM sources)
        """).fetchall()
        broken_fks["report_tables_missing_source"] = [dict(r) for r in broken]

        # source_dependencies pointing to non-existent sources
        broken = db.execute("""
            SELECT sd.id, sd.source_id, sd.depends_on_id
            FROM source_dependencies sd
            WHERE sd.source_id NOT IN (SELECT id FROM sources)
               OR sd.depends_on_id NOT IN (SELECT id FROM sources)
        """).fetchall()
        broken_fks["source_deps_missing_source"] = [dict(r) for r in broken]

        report["broken_fk_references"] = broken_fks

        # ── Source Dependencies ──
        dep_rows = db.execute("""
            SELECT sd.source_id, s1.name AS source_name,
                   sd.depends_on_id, s2.name AS depends_on_name,
                   sd.discovered_by
            FROM source_dependencies sd
            LEFT JOIN sources s1 ON s1.id = sd.source_id
            LEFT JOIN sources s2 ON s2.id = sd.depends_on_id
            ORDER BY s1.name
        """).fetchall()
        report["source_dependencies"] = [
            {
                "source_id": r["source_id"], "source_name": r["source_name"],
                "depends_on_id": r["depends_on_id"], "depends_on_name": r["depends_on_name"],
                "discovered_by": r["discovered_by"],
            }
            for r in dep_rows
        ]

        # ── Reports with No Sources ──
        no_src = db.execute("""
            SELECT r.id, r.name
            FROM reports r
            WHERE NOT EXISTS (
                SELECT 1 FROM report_tables rt
                WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
            )
        """).fetchall()
        report["reports_with_no_sources"] = [{"id": r["id"], "name": r["name"]} for r in no_src]

        # ── Recent Scan Runs ──
        scans = db.execute(
            "SELECT id, started_at, finished_at, status, reports_scanned, sources_found, new_sources, changed_queries, broken_refs, log FROM scan_runs ORDER BY id DESC LIMIT 5"
        ).fetchall()
        report["recent_scans"] = [dict(r) for r in scans]

        # ── Source Type Distribution ──
        type_dist = db.execute(
            "SELECT type, COUNT(*) AS count FROM sources WHERE archived = 0 GROUP BY type ORDER BY count DESC"
        ).fetchall()
        report["source_type_distribution"] = {r["type"]: r["count"] for r in type_dist}

        # ── Probe Status Distribution ──
        probe_dist = db.execute("""
            SELECT sp.status, COUNT(*) AS count
            FROM sources s
            JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE s.archived = 0
            GROUP BY sp.status
        """).fetchall()
        report["probe_status_distribution"] = {r["status"]: r["count"] for r in probe_dist}

    return report


@router.get("/pbi-usage-days")
def get_usage_days():
    """Return list of days already synced for PBI usage."""
    with get_db() as db:
        rows = db.execute("SELECT date FROM pbi_usage_days ORDER BY date").fetchall()
    return [r["date"] for r in rows]


@router.post("/pbi-usage-import")
def import_pbi_usage(request: Request, data: dict = fastapi.Body(...)):
    """Import PBI usage data from PS1 script."""
    _require_scan_access(request)
    return import_pbi_usage_data(data)


@router.post("/pbi-usage-sync")
def do_pbi_usage_sync(request: Request):
    """Sync usage from configured CSVs, falling back to the legacy PS1 sync."""
    _require_scan_access(request)
    stop_result = stop_pbi_sync_processes("New Power BI usage sync started.")
    generation = (stop_result.get("scanner") or {}).get("generation")
    with get_db() as db:
        csv_result = sync_usage_from_csv(db, force=True)
        if csv_result.get("status") != "skipped":
            csv_result["stop"] = stop_result
            if csv_result.get("status") in {"completed", "success"}:
                _record_sync_run(
                    "usage",
                    "completed",
                    csv_result.get("message") or "Power BI usage CSV sync completed.",
                    csv_result,
                )
            return csv_result
    return trigger_pbi_usage_sync(cancel_existing=False, cancel_generation=generation)
