"""Small, read-only row progress derived from a run's frozen work and evidence."""
from __future__ import annotations

import json


def _load(value, fallback):
    try:
        result = json.loads(value or "null")
        return result if isinstance(result, type(fallback)) else fallback
    except (ValueError, TypeError):
        return fallback


def _text(value, fallback=""):
    return str(value or fallback).strip()[:300]


def row_progress(db, run):
    job = _load(run["job_json"], {})
    detail = _load(run["progress_json"], {})
    artifacts = _load(run["artifact_json"], [])
    status = run["status"]
    stage = detail.get("stage") or status
    source = job.get("flow", {}).get("source_type", "portal")
    sql_only = job.get("job_type") == "sql_retry"
    downloads = job.get("downloads") or {}
    report = job.get("report") or {}
    exports = (report.get("download_links") if job.get("site", {}).get("adapter") == "asap_portal" else None) or report.get("export_views") or [None]
    count = len(exports) * len(downloads.get("periods") or [None]) if source == "portal" else 1
    # Old/malformed snapshots cannot support a trustworthy denominator.
    known = bool(job.get("flow") and (sql_only or job.get("downloads") or source in {"file", "outlook"}))
    tasks = [dict(row) for row in db.execute(
        """SELECT id,ordinal,state,worker_id,progress_json FROM flow_download_tasks
           WHERE run_id=? ORDER BY ordinal""", (run["id"],),
    )]
    if tasks:
        count = len(tasks)
    stages = {row[0] for row in db.execute(
        "SELECT DISTINCT stage FROM flow_run_events WHERE run_id=?", (run["id"],),
    )}
    stages.add(stage)
    later = {"direct_publish", "publish_complete", "transformation", "transformation_complete", "complete"}
    after_download = bool(stages & later or any(str(item).startswith("sql_") for item in stages))
    saved = {str(item.get("bundle_index") or json.dumps([item.get("export_view"), item.get("period_key")]))
             for item in artifacts if isinstance(item, dict) and item.get("status") == "saved"}
    acquired = sum(task["state"] == "succeeded" for task in tasks) if tasks else min(count, len(saved))
    if after_download:
        acquired = count
    if source in {"file", "outlook"} and stages & {"file_normalization", "file_validation"}:
        acquired = count
    prepare_stages = {"configuring", "report_execution", "file_export", "file_transfer", "file_normalization",
                      "file_validation", "local_file_copy", "outlook_attachment_transfer", "parallel_downloads"}
    prepared = bool(acquired or after_download or stages & prepare_stages)
    normalized = after_download or (acquired == count and bool(saved or tasks))
    work = []
    if not sql_only:
        work += [("Prepare run", 1, int(prepared)),
                 ("Read file" if source == "file" else "Download", count, acquired),
                 ("Prepare files", 1, int(normalized))]
        if downloads.get("output_mode") == "direct_replace" and source != "file":
            work.append(("Publish files", 1, int(bool(stages & {"publish_complete", "transformation", "transformation_complete", "sql_insertion", "complete"}))))
        if job.get("transformation", {}).get("enabled"):
            work.append(("Transform", 1, int(bool(stages & {"transformation_complete", "sql_insertion", "complete"}))))
    else:
        work.append(("Read saved files", 1, int(any(str(item).startswith("sql_") and item != "sql_retry" for item in stages))))
    if job.get("sql_handoff", {}).get("enabled") or sql_only:
        work.append(("Insert into SQL", 1, int("sql_insertion_complete" in stages or "complete" in stages)))
    work.append(("Finish", 1, int(status == "succeeded")))
    no_op = bool(detail.get("no_op") or stages & {"local_file_no_op", "outlook_no_op"})
    if no_op:
        work = [("Check source", 1, 1), ("Finish", 1, int(status == "succeeded"))]
    phases = [{"label": label, "total": total, "completed": total if status == "succeeded" else min(total, done)}
              for label, total, done in work]
    total = sum(item["total"] for item in phases)
    completed = sum(item["completed"] for item in phases)
    runners = {}
    active = status in {"claimed", "running"}
    if active and run["worker_id"]:
        coordinating = bool(tasks and not after_download)
        runners[run["worker_id"]] = {
            "id": run["worker_id"], "message": _text(detail.get("message"), "Preparing run"),
            "stage": _text(stage), "label": "Coordinating downloads" if coordinating else "Run progress",
            "completed": None if coordinating or not known else completed,
            "total": None if coordinating or not known else total,
            "phases": [] if coordinating or not known else phases,
        }
    if active:
        for task in tasks:
            if task["state"] not in {"claimed", "cancelling"} or not task["worker_id"]:
                continue
            progress = _load(task["progress_json"], {})
            task_stage = _text(progress.get("stage"), "download")
            # Discrete, observed milestones only; do not invent byte progress
            # while the portal is generating or transferring a file.
            observed = set(progress.get('_download_milestones') or []) | {task_stage}
            downloaded = bool(observed & {"file_normalization", "file_validation"})
            prepared = downloaded or bool(observed & {"file_export", "file_transfer"})
            task_phases = [
                {"label": "Prepare export", "completed": int(prepared), "total": 1},
                {"label": "Download", "completed": int(downloaded), "total": 1},
                {"label": "Prepare file", "completed": 0, "total": 1},
            ]
            runners[task["worker_id"]] = {
                "id": task["worker_id"], "stage": task_stage,
                "task_id": task["id"], "label": f"Export {task['ordinal']} of {count}",
                "completed": sum(phase["completed"] for phase in task_phases),
                "total": 3, "phases": task_phases,
                "message": _text(progress.get("message"), f"Downloading export {task['ordinal']} of {count}"),
            }
    message = _text(detail.get("message"))
    if tasks and active and not after_download:
        message = f"Downloaded {acquired} of {count} exports"
        current = [item["message"] for item in runners.values() if item["stage"] != "parallel_downloads"]
        if current:
            message += " · " + current[0]
    if status == "queued":
        message = "Waiting for a worker"
    elif status == "claimed" and not message:
        message = "Worker assigned · preparing run"
    elif status == "succeeded":
        message = "No changes · nothing to process" if no_op else "Completed"
    elif status == "cancelled":
        message = "Cancelled"
    elif status == "failed":
        message = "Failed · open run log for details"
    return {"stage": _text(stage), "message": message or "Preparing run", "runners": list(runners.values()),
            "completed": completed if known else None, "total": total if known else None,
            "phases": phases if known else [], "no_op": no_op}
