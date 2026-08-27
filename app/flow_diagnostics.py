"""Report-scoped Flow target diagnostics shared by lineage and pipelines.

The exact identity resolver remains the only authority for execution.  This
module adds report-scope and legacy display-name evidence for presentation,
without mutating Flow links or granting fuzzy matches execution power.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from app.scanner.lifecycle import normalize_scan_status, parse_components
from app.source_identity import inspect_flow_target, normalize_server


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _normalized_object_name(value: str | None) -> str:
    value = (value or "").strip().casefold()
    value = value.replace('"', "").replace("`", "").replace("[", "").replace("]", "")
    return re.sub(r"[\\/]+", ".", value).strip(".")


def source_matches_legacy_flow_target(source: Mapping, flow: Mapping) -> bool:
    """Return presentation-only suffix/name evidence for a Flow target."""
    table = _normalized_object_name(_row_value(flow, "sql_table"))
    schema = _normalized_object_name(_row_value(flow, "sql_schema"))
    if not table:
        return False
    target = f"{schema}.{table}" if schema else table
    candidates = {
        _normalized_object_name(_row_value(source, "name")),
        _normalized_object_name(_row_value(source, "connection_info")),
    }
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == target or candidate.endswith(f".{target}"):
            return True
        if not schema and candidate.rsplit(".", 1)[-1] == table:
            return True
    return False


def _target(flow, server: str) -> dict:
    return {
        "server": normalize_server(server),
        "database": (_row_value(flow, "sql_database") or "").strip(),
        "schema": (_row_value(flow, "sql_schema") or "").strip(),
        "table": (_row_value(flow, "sql_table") or "").strip(),
    }


def _server_mismatch_ids(db, target: Mapping) -> list[int]:
    """Find exact database/schema/relation identities on another server."""
    if not all(target.get(key) for key in ("database", "schema", "table")):
        return []
    rows = db.execute(
        """SELECT spi.source_id, spi.server_name
           FROM source_postgres_identities spi
           JOIN sources s ON s.id=spi.source_id
           WHERE spi.database_name=? AND spi.schema_name=? AND spi.relation_name=?
             AND COALESCE(s.archived, 0)=0
           ORDER BY spi.source_id""",
        (target["database"], target["schema"], target["table"]),
    ).fetchall()
    wanted_server = normalize_server(target.get("server"))
    return [
        int(row["source_id"])
        for row in rows
        if normalize_server(row["server_name"]) != wanted_server
    ]


def _latest_postgres_dependencies(db) -> dict:
    row = db.execute(
        """SELECT id, components_json
           FROM scan_runs
           ORDER BY id DESC
           LIMIT 1"""
    ).fetchone()
    if row is None:
        return {"status": "not_scanned", "scan_run_id": None, "databases": {}}
    components = parse_components(row["components_json"]) or {}
    component = components.get("postgres_dependencies")
    if not isinstance(component, Mapping):
        return {"status": "unknown", "scan_run_id": int(row["id"]), "databases": {}}
    databases = component.get("databases")
    return {
        "status": normalize_scan_status(component.get("status")),
        "scan_run_id": int(row["id"]),
        "databases": dict(databases) if isinstance(databases, Mapping) else {},
    }


def _diagnostic_message(reason_code: str | None, flow_name: str) -> str:
    messages = {
        None: "The exact SQL target is connected to this report.",
        "incomplete_target": "The Flow SQL target is incomplete.",
        "target_not_discovered": "No exact source identity has been discovered for this target.",
        "ambiguous_target": "Multiple exact source identities match this target.",
        "stale_target_link": "The saved target source no longer exists or is archived.",
        "target_changed": "The saved source identity no longer matches the Flow SQL target.",
        "server_mismatch": "This target was discovered on a different PostgreSQL server.",
        "outside_report_closure": "The exact Flow target is not in this report's lineage.",
        "legacy_display_match": (
            "A source display name resembles this target, but it is not an exact identity match."
        ),
    }
    return messages.get(reason_code, f"Flow '{flow_name}' is not connected to this report.")


def _recommended_action(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    if reason_code in {"target_not_discovered", "server_mismatch"}:
        return "recheck_lineage"
    return "edit_flow"


def build_flow_diagnostics(
    db,
    report_source_ids: Iterable[int],
    *,
    server: str,
    report_sources: Iterable[Mapping] | None = None,
) -> dict:
    """Build one pure, additive Flow diagnostic contract for a report.

    Only an exact resolver result with ``status == 'confirmed'`` and an
    effective source inside the report closure is executable.  Invalid stored
    IDs and exact candidates in that closure are blockers.  Legacy display
    matches are presentation warnings only.
    """
    closure = {int(source_id) for source_id in report_source_ids if source_id is not None}
    if report_sources is None:
        if closure:
            placeholders = ",".join("?" for _ in closure)
            rows = db.execute(
                f"""SELECT id, name, connection_info
                    FROM sources WHERE id IN ({placeholders}) ORDER BY id""",
                sorted(closure),
            ).fetchall()
            report_sources = [dict(row) for row in rows]
        else:
            report_sources = []
    else:
        report_sources = list(report_sources)

    download_only_count = int(
        db.execute(
            "SELECT COUNT(*) AS count FROM flows WHERE COALESCE(sql_handoff_enabled, 0)=0"
        ).fetchone()["count"]
    )
    flow_rows = db.execute(
        """SELECT id, name, browser_mode, enabled, sql_handoff_enabled,
                  sql_database, sql_schema, sql_table, sql_target_source_id,
                  updated_at, last_run_at, last_success_at, last_status, last_error
           FROM flows
           WHERE COALESCE(sql_handoff_enabled, 0)=1
           ORDER BY name, id"""
    ).fetchall()

    items: list[dict] = []
    included_count = 0
    for row in flow_rows:
        flow = dict(row)
        target = _target(flow, server)
        inspection = inspect_flow_target(db, flow, server=server)
        persisted_id = inspection.get("persisted_source_id")
        effective_id = inspection.get("effective_source_id")
        exact_ids = sorted({int(value) for value in inspection.get("matches", [])})
        exact_in_report = bool(set(exact_ids) & closure)
        persisted_in_report = (
            persisted_id is not None and int(persisted_id) in closure
        )
        status = inspection.get("status") or "unresolved"
        included = status == "confirmed" and effective_id is not None and int(effective_id) in closure
        executable = included
        candidate_ids = list(exact_ids)

        if included:
            included_count += 1
            reason_code = None
            severity = "none"
            scope_status = "confirmed_in_report"
        elif status == "confirmed" and effective_id is not None:
            reason_code = "outside_report_closure"
            severity = "warning"
            scope_status = "outside_report_closure"
        elif status in {"stale", "target_changed"}:
            reason_code = inspection.get("reason_code") or (
                "stale_target_link" if status == "stale" else "target_changed"
            )
            if exact_in_report or persisted_in_report:
                severity = "blocker"
                scope_status = "candidate_in_report"
            elif candidate_ids:
                severity = "warning"
                scope_status = "outside_report_closure"
            else:
                severity = "warning"
                scope_status = "no_report_evidence"
        elif inspection.get("reason_code") == "incomplete_target":
            reason_code = "incomplete_target"
            severity = "blocker" if persisted_in_report else "warning"
            scope_status = "candidate_in_report" if persisted_in_report else "no_report_evidence"
        elif status == "ambiguous":
            reason_code = "ambiguous_target"
            severity = "blocker" if exact_in_report else "warning"
            scope_status = "candidate_in_report" if exact_in_report else "outside_report_closure"
        else:
            mismatch_ids = _server_mismatch_ids(db, target)
            mismatch_in_report = bool(set(mismatch_ids) & closure)
            if mismatch_ids:
                candidate_ids = mismatch_ids
                reason_code = "server_mismatch"
                severity = "blocker" if mismatch_in_report else "warning"
                scope_status = (
                    "candidate_in_report" if mismatch_in_report else "outside_report_closure"
                )
            else:
                legacy_ids = sorted(
                    {
                        int(source["id"])
                        for source in report_sources
                        if source_matches_legacy_flow_target(source, flow)
                    }
                )
                if legacy_ids:
                    candidate_ids = legacy_ids
                    reason_code = "legacy_display_match"
                    severity = "warning"
                    scope_status = "candidate_in_report"
                else:
                    reason_code = "target_not_discovered"
                    severity = "warning"
                    scope_status = "no_report_evidence"

        item = {
            "id": int(flow["id"]),
            "name": flow["name"],
            "target": target,
            "persisted_source_id": int(persisted_id) if persisted_id is not None else None,
            "effective_source_id": int(effective_id) if effective_id is not None else None,
            "candidate_source_ids": candidate_ids,
            "link_status": status,
            "scope_status": scope_status,
            "reason_code": reason_code,
            "severity": severity,
            "message": _diagnostic_message(reason_code, flow["name"]),
            "recommended_action": _recommended_action(reason_code),
            "executable": executable,
        }
        items.append(item)

    return {
        "included_count": included_count,
        "excluded_count": len(items) - included_count,
        "download_only_count": download_only_count,
        "items": items,
        "postgres_dependencies": _latest_postgres_dependencies(db),
    }


def included_flow_ids(flow_diagnostics: Mapping) -> set[int]:
    """Return the exact report-scoped Flow IDs authorized for execution."""
    return {
        int(item["id"])
        for item in flow_diagnostics.get("items", [])
        if item.get("executable")
    }


def legacy_flow_suggestions(flow_diagnostics: Mapping) -> list[dict]:
    """Preserve the pre-diagnostics compatibility list for API consumers."""
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "target_source_ids": item["candidate_source_ids"],
            "sql_database": item["target"]["database"],
            "sql_schema": item["target"]["schema"],
            "sql_table": item["target"]["table"],
            "executable": False,
            "reason": item["message"],
        }
        for item in flow_diagnostics.get("items", [])
        if item.get("reason_code") == "legacy_display_match"
    ]


def diagnostic_blocker_messages(flow_diagnostics: Mapping) -> list[str]:
    """Translate blocker diagnostics into pipeline preflight messages."""
    return [
        f"Flow '{item['name']}': {item['message']}"
        for item in flow_diagnostics.get("items", [])
        if item.get("severity") == "blocker"
    ]
