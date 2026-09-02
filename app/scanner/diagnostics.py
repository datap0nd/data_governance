"""Safe, operator-facing diagnostics shared by scanner jobs and modules."""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.scanner.lifecycle import normalize_scan_status


_SAFE_FACT_KEYS = frozenset({
    "ambiguous_provider_count",
    "catalog_reports_active",
    "changed_queries",
    "deps_created",
    "failed_days",
    "governed_mvs",
    "jobs_found",
    "matched",
    "mv_jobs",
    "mvs_found",
    "prior_evidence_retained",
    "reports_discovered",
    "reports_preserved_incomplete",
    "reports_scanned",
    "requested_days",
    "schedule_evidence_needed",
    "sources_found",
    "successful_days",
    "zero_activity_days",
})
_REMEDIATION = {
    "postgres_credentials_not_configured": [
        "Configure PGHOST, PGUSER, and PGPASSWORD for the read-only scanner account.",
    ],
    "postgres_connection_failed": [
        "Verify the PostgreSQL host, port, database, credentials, and network access.",
    ],
    "pg_cron_not_installed": [
        "Install and enable pg_cron in the PostgreSQL database if schedule discovery is required.",
    ],
    "pg_cron_permission_denied": [
        "Grant the scanner account USAGE on schema cron and SELECT on cron.job.",
    ],
    "pg_cron_job_query_failed": [
        "Verify that the scanner account can read cron.job, then rerun PostgreSQL schedules.",
    ],
    "pg_cron_run_history_unavailable": [
        "Grant SELECT on cron.job_run_details to include last-run history; schedules were still retained.",
    ],
    "pg_cron_snapshot_incomplete": [
        "Confirm that the scanner role can see jobs owned by every expected pg_cron user.",
    ],
    "pg_cron_no_visible_jobs": [
        "Confirm the scanner account can see all expected cron jobs before clearing prior schedule evidence.",
    ],
    "no_eligible_targets": [
        "No action is required unless PostgreSQL sources or Flow SQL targets were expected.",
    ],
    "module_execution_failed": [
        "Review the Metronome server log for the module run and rerun after correcting the underlying issue.",
    ],
}
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*([^\s,;]+)"
)
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")


def _safe_text(value: Any, limit: int = 1000) -> str:
    text = str(value or "").strip()
    text = _CREDENTIAL_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _URL_USERINFO.sub(r"\1[redacted]@", text)
    return text[:limit]


def result_message(job_type: str, status: str, result: Mapping[str, Any]) -> str:
    """Return concise operator prose without depending on raw exceptions."""
    status = normalize_scan_status(status)
    if job_type == "postgres_lineage":
        databases = result.get("databases")
        if normalize_scan_status(result.get("status")) == "not_requested":
            return "No eligible PostgreSQL sources or Flow SQL targets required lineage discovery."
        if isinstance(databases, Mapping):
            failed = [
                f"{name} ({details.get('stage') or 'scan'})"
                for name, details in databases.items()
                if isinstance(details, Mapping)
                and normalize_scan_status(details.get("status")) == "failed"
            ]
            if failed:
                return "Lineage could not be refreshed for: " + ", ".join(failed)
        reconciliation = result.get("report_identity_reconciliation")
        if status == "completed_with_warnings" and isinstance(reconciliation, Mapping):
            issues = reconciliation.get("issues")
            issues = issues if isinstance(issues, list) else []
            unconfigured = next((
                issue for issue in issues
                if isinstance(issue, Mapping)
                and issue.get("reason_code") == "unconfigured_catalog_endpoint"
            ), None)
            if unconfigured is not None:
                endpoint = "/".join(str(value) for value in (
                    unconfigured.get("server"), unconfigured.get("database")
                ) if value)
                return (
                    "Lineage needs attention: no configured catalog connection "
                    f"for {endpoint or 'the report endpoint'}."
                )
            if issues:
                count = len(issues)
                return (
                    f"Lineage rechecked with warnings: {count} report source "
                    f"issue{'s' if count != 1 else ''} need attention."
                )
        if status == "completed_with_warnings":
            unconfigured_targets = result.get("unconfigured_catalog_targets")
            if isinstance(unconfigured_targets, list) and unconfigured_targets:
                target = unconfigured_targets[0]
                if isinstance(target, Mapping):
                    endpoint = "/".join(str(value) for value in (
                        target.get("server"), target.get("database")
                    ) if value)
                    return (
                        "Lineage needs attention: no configured catalog connection "
                        f"for active source {endpoint or 'endpoint'}."
                    )
            if result.get("unattempted_catalog_targets"):
                return (
                    "Lineage targets changed while the recheck was running; "
                    "rerun lineage to scan the final target set."
                )
            if result.get("superseded_cleanup_failures"):
                return (
                    "Lineage refreshed, but obsolete query-change alerts could not "
                    "be retired; rerun lineage or review Scanner details."
                )
        if status == "completed_with_warnings" and isinstance(databases, Mapping):
            flow_attention = []
            total_flow_targets = 0
            for name, details in databases.items():
                if not isinstance(details, Mapping) or normalize_scan_status(details.get("status")) == "superseded":
                    continue
                if details.get("flow_reconciliation_error"):
                    return (
                        "Lineage refreshed, but final Flow target matching could not "
                        f"be completed for {name}."
                    )
                try:
                    count = int(details.get("flow_targets_needing_attention") or 0)
                except (TypeError, ValueError):
                    count = 0
                if count > 0:
                    total_flow_targets += count
                    flow_attention.append(str(name))
            if total_flow_targets:
                return (
                    "Lineage refreshed, but "
                    f"{total_flow_targets} Flow SQL target"
                    f"{'s are' if total_flow_targets != 1 else ' is'} still not connected "
                    "to an exact catalog source"
                    f" ({', '.join(flow_attention)})."
                )
        if status == "completed":
            repaired = 0
            if isinstance(reconciliation, Mapping):
                repaired = int(reconciliation.get("claimed") or 0) + int(
                    reconciliation.get("relinked") or 0
                )
            repair_message = (
                f", {repaired} report source{'s' if repaired != 1 else ''} repaired"
                if repaired else ""
            )
            return (
                f"Lineage refreshed: {int(result.get('mvs_found') or 0)} materialized "
                f"views, {int(result.get('deps_created') or 0)} dependencies{repair_message}."
            )
    diagnostic = result.get("diagnostic")
    if isinstance(diagnostic, Mapping) and diagnostic.get("operator_summary"):
        return _safe_text(diagnostic["operator_summary"])
    explicit = result.get("message")
    if explicit and status != "failed":
        return _safe_text(explicit)
    labels = {
        "completed": "Completed.",
        "completed_with_warnings": "Completed with warnings.",
        "failed": "Failed; review the module details and server log.",
        "stopped": "Stopped.",
        "skipped": "Skipped because its prerequisite was not available.",
        "not_requested": "Not requested.",
    }
    return labels.get(status, status.replace("_", " ").capitalize() + ".")


def diagnostic_for_result(
    module_key: str,
    status: str,
    result: Mapping[str, Any],
    *,
    fallback_summary: str | None = None,
) -> dict[str, Any]:
    """Build an allowlisted diagnostic before generic payload redaction runs."""
    status = normalize_scan_status(status)
    existing = result.get("diagnostic")
    if isinstance(existing, Mapping):
        reason_code = _safe_text(existing.get("reason_code"), 120) or None
        operator_summary = _safe_text(existing.get("operator_summary"))
        health_impact = str(existing.get("health_impact") or "").casefold()
        remediation = existing.get("remediation")
        facts = existing.get("facts")
    else:
        reason_code = _safe_text(result.get("reason_code"), 120) or None
        operator_summary = ""
        health_impact = ""
        remediation = None
        facts = None

    if module_key == "postgres_lineage" and status == "not_requested":
        reason_code = reason_code or "no_eligible_targets"
    if not reason_code:
        reason_code = {
            "failed": "module_execution_failed",
            "stopped": "module_stopped",
            "skipped": "module_prerequisite_unavailable",
            "not_requested": "module_not_requested",
            "completed_with_warnings": "module_completed_with_warnings",
        }.get(status, "module_completed")
    if not operator_summary:
        if status == "failed" and reason_code == "module_execution_failed":
            label = module_key.replace("_", " ").capitalize()
            operator_summary = f"{label} failed; review the Metronome server log for this run."
        elif fallback_summary and reason_code not in {
            "module_completed", "module_completed_with_warnings", "module_stopped",
            "module_prerequisite_unavailable", "module_not_requested",
        }:
            operator_summary = _safe_text(fallback_summary)
        else:
            operator_summary = result_message(module_key, status, result)
            if operator_summary in {"Completed with warnings.", "Stopped."} and fallback_summary:
                operator_summary = _safe_text(fallback_summary)
    if health_impact not in {"none", "warning", "error"}:
        health_impact = "error" if status == "failed" else (
            "warning" if status == "completed_with_warnings" else "none"
        )
    if not isinstance(remediation, (list, tuple)):
        remediation = _REMEDIATION.get(reason_code, [])
    safe_remediation = [_safe_text(item, 500) for item in list(remediation)[:5] if _safe_text(item, 500)]
    source_facts = facts if isinstance(facts, Mapping) else result
    safe_facts = {
        str(key): value
        for key, value in source_facts.items()
        if str(key) in _SAFE_FACT_KEYS and (value is None or isinstance(value, (bool, int, float, str)))
    }
    return {
        "health_impact": health_impact,
        "reason_code": reason_code,
        "operator_summary": operator_summary,
        "remediation": safe_remediation,
        "facts": safe_facts,
    }


def with_diagnostic(
    module_key: str,
    status: str,
    result: Mapping[str, Any] | None,
    *,
    fallback_summary: str | None = None,
) -> dict[str, Any]:
    payload = dict(result or {})
    payload["status"] = normalize_scan_status(status)
    payload["diagnostic"] = diagnostic_for_result(
        module_key, payload["status"], payload, fallback_summary=fallback_summary
    )
    return payload
