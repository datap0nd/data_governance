"""Report-scoped Flow target diagnostics shared by lineage and pipelines.

Structured PostgreSQL coordinates are the execution authority. Conservative
file-output matches are visible lineage candidates only: Flow workers retain
each run in a versioned subfolder, so a filename match does not prove that a
new run updates the exact file consumed by Power BI.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping

from app.scanner.lifecycle import normalize_scan_status, parse_components
from app.source_identity import (
    inspect_file_flow_target,
    inspect_flow_target,
    normalize_server,
)


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


def _server_alias_lineage_gaps(
    db,
    report_root_source_ids: set[int],
    *,
    catalog_server: str,
) -> list[dict]:
    """Find report sources disconnected from catalog edges by an exact host split.

    This is diagnostic only. Matching database/schema/relation names on two
    hosts is not proof that they are the same physical server, so Metronome
    must never merge the identities automatically. The narrow signal here is
    a report-scoped identity with no dependency edges while an otherwise equal
    identity on another host owns catalog-discovered dependency edges.
    """
    wanted_catalog_server = normalize_server(catalog_server)
    if not report_root_source_ids or not wanted_catalog_server:
        return []
    placeholders = ",".join("?" for _ in report_root_source_ids)
    rows = db.execute(
        f"""SELECT report_identity.source_id AS report_source_id,
                   report_source.name AS report_source_name,
                   report_identity.server_name AS report_server,
                   report_identity.database_name,
                   report_identity.schema_name,
                   report_identity.relation_name,
                   catalog_identity.source_id AS catalog_source_id,
                   catalog_identity.server_name AS catalog_server,
                   COUNT(catalog_edge.depends_on_id) AS dependency_count
              FROM source_postgres_identities report_identity
              JOIN sources report_source
                ON report_source.id=report_identity.source_id
              JOIN source_postgres_identities catalog_identity
                ON catalog_identity.source_id!=report_identity.source_id
               AND catalog_identity.database_name=report_identity.database_name
               AND catalog_identity.schema_name=report_identity.schema_name
               AND catalog_identity.relation_name=report_identity.relation_name
              JOIN sources catalog_source
                ON catalog_source.id=catalog_identity.source_id
              JOIN source_dependencies catalog_edge
                ON catalog_edge.source_id=catalog_identity.source_id
               AND catalog_edge.discovered_by='pg_matviews'
              LEFT JOIN source_dependencies report_edge
                ON report_edge.source_id=report_identity.source_id
               AND report_edge.discovered_by='pg_matviews'
             WHERE report_identity.source_id IN ({placeholders})
               AND report_edge.source_id IS NULL
               AND COALESCE(report_source.archived, 0)=0
               AND COALESCE(catalog_source.archived, 0)=0
             GROUP BY report_identity.source_id, report_source.name,
                      report_identity.server_name, report_identity.database_name,
                      report_identity.schema_name, report_identity.relation_name,
                      catalog_identity.source_id, catalog_identity.server_name
             ORDER BY report_source.name, report_identity.source_id,
                      catalog_identity.source_id""",
        sorted(report_root_source_ids),
    ).fetchall()
    gaps = []
    seen: set[tuple[int, str]] = set()
    for row in rows:
        report_source_id = int(row["report_source_id"])
        catalog_source_id = int(row["catalog_source_id"])
        report_server = normalize_server(row["report_server"])
        discovered_catalog_server = normalize_server(row["catalog_server"])
        if (
            not report_server
            or not discovered_catalog_server
            or report_server == discovered_catalog_server
            or discovered_catalog_server != wanted_catalog_server
            or catalog_source_id in report_root_source_ids
        ):
            continue
        key = (report_source_id, discovered_catalog_server)
        if key in seen:
            continue
        seen.add(key)
        gaps.append(
            {
                "report_source_id": report_source_id,
                "report_source_name": row["report_source_name"],
                "report_server": report_server,
                "catalog_source_id": catalog_source_id,
                "catalog_server": discovered_catalog_server,
                "database": row["database_name"],
                "schema": row["schema_name"],
                "table": row["relation_name"],
                "dependency_count": int(row["dependency_count"] or 0),
            }
        )
    return gaps


def _latest_postgres_dependencies(db) -> dict:
    row = db.execute(
        """SELECT id, finished_at, components_json
           FROM scan_runs
           ORDER BY id DESC
           LIMIT 1"""
    ).fetchone()
    job = db.execute(
        """SELECT id, status, result_json, finished_at
             FROM scanner_jobs
            WHERE job_type='postgres_lineage'
              AND status IN ('completed','completed_with_warnings','failed','stopped')
            ORDER BY finished_at DESC, id DESC
            LIMIT 1"""
    ).fetchone()

    def timestamp(value) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    # A focused Lineage Recheck does not create a synthetic full scan row.
    # Prefer its durable result when it is newer, so Pipelines immediately
    # reflects the operation the user just watched complete in Scanner.
    if job is not None and (
        row is None or timestamp(job["finished_at"]) >= timestamp(row["finished_at"])
    ):
        try:
            result = json.loads(job["result_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            result = {}
        if not isinstance(result, Mapping):
            result = {}
        databases = result.get("databases")
        return {
            "status": normalize_scan_status(result.get("status") or job["status"]),
            "scan_run_id": None,
            "scanner_job_id": int(job["id"]),
            "databases": dict(databases) if isinstance(databases, Mapping) else {},
        }
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
        "ambiguous_file_target": (
            "More than one file source has this output filename; no automatic link was made."
        ),
        "file_output_candidate": (
            "The filename matches a report source, but Flow runs use versioned output folders. "
            "This is a possible lineage link only and will not run automatically in the Pipeline."
        ),
        "dynamic_file_target": (
            "This Flow uses a changing output filename, so it cannot be linked automatically."
        ),
        "file_target_not_in_report": "This output file is not used by this report.",
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
    report_root_source_ids: Iterable[int] | None = None,
) -> dict:
    """Build one pure, additive Flow diagnostic contract for a report.

    Only a SQL resolver result with ``status == 'confirmed'`` and an effective
    source inside the report closure is executable. File path/basename matches
    remain presentation-only until Flows have a stable published-output
    identity. Legacy display matches are presentation warnings only.
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

    flow_rows = db.execute(
        """SELECT id, name, browser_mode, enabled, target_folder,
                  filename_template, sql_handoff_enabled,
                  sql_database, sql_schema, sql_table, sql_target_source_id,
                  updated_at, last_run_at, last_success_at, last_status, last_error
           FROM flows
           ORDER BY name, id"""
    ).fetchall()

    items: list[dict] = []
    included_count = 0
    for row in flow_rows:
        flow = dict(row)
        sql_target = bool(flow.get("sql_handoff_enabled"))
        target_kind = "postgresql" if sql_target else "file"
        if sql_target:
            target = _target(flow, server)
            inspection = inspect_flow_target(db, flow, server=server)
        else:
            inspection = inspect_file_flow_target(
                db,
                flow,
                closure,
                report_sources=report_sources,
            )
            target = inspection["target"]
        persisted_id = inspection.get("persisted_source_id")
        effective_id = inspection.get("effective_source_id")
        exact_ids = sorted({int(value) for value in inspection.get("matches", [])})
        exact_in_report = bool(set(exact_ids) & closure)
        persisted_in_report = (
            persisted_id is not None and int(persisted_id) in closure
        )
        status = inspection.get("status") or "unresolved"
        confirmed_in_report = (
            status == "confirmed"
            and effective_id is not None
            and int(effective_id) in closure
        )
        included = sql_target and confirmed_in_report
        executable = included
        candidate_ids = list(exact_ids)

        if not sql_target and confirmed_in_report:
            reason_code = "file_output_candidate"
            severity = "warning"
            scope_status = "candidate_in_report"
            status = "candidate"
        elif included:
            included_count += 1
            reason_code = None
            severity = "none"
            scope_status = "confirmed_in_report"
        elif status == "confirmed" and effective_id is not None:
            reason_code = "outside_report_closure"
            severity = "warning"
            scope_status = "outside_report_closure"
        elif not sql_target and status == "ambiguous":
            reason_code = inspection.get("reason_code") or "ambiguous_file_target"
            severity = "warning"
            scope_status = (
                "candidate_in_report" if exact_in_report else "outside_report_closure"
            )
        elif not sql_target:
            reason_code = inspection.get("reason_code") or "file_target_not_in_report"
            severity = "warning"
            scope_status = "no_report_evidence"
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

        # Pipeline diagnostics are report-scoped.  A global list of every Flow
        # that does not feed this report is both noisy and misleading.  Keep
        # connected Flows plus unresolved evidence that is actually inside the
        # selected report's recursive source closure.
        relevant = included or scope_status == "candidate_in_report"
        if not relevant:
            continue

        item = {
            "diagnostic_kind": "flow",
            "id": int(flow["id"]),
            "name": flow["name"],
            "target": target,
            "target_kind": target_kind,
            "match_strategy": inspection.get("match_strategy"),
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

    # Host-alias gaps are meaningful only for the report's direct Power BI
    # sources. Running this check across the recursive closure can falsely
    # flag a legitimate upstream leaf that merely shares a name with an
    # unrelated object on another server. Callers that cannot identify direct
    # roots omit the diagnostic rather than risk a false blocker.
    roots = (
        {int(source_id) for source_id in report_root_source_ids if source_id is not None}
        if report_root_source_ids is not None
        else set()
    )
    for gap in _server_alias_lineage_gaps(
        db,
        roots & closure,
        catalog_server=server,
    ):
        relation = ".".join(
            value for value in (gap["database"], gap["schema"], gap["table"]) if value
        )
        items.append(
            {
                "diagnostic_kind": "lineage_gap",
                "id": None,
                "name": gap["report_source_name"] or "PostgreSQL source",
                "target": {
                    "server": gap["report_server"],
                    "database": gap["database"],
                    "schema": gap["schema"],
                    "table": gap["table"],
                },
                "target_kind": "postgresql",
                "match_strategy": None,
                "persisted_source_id": gap["report_source_id"],
                "effective_source_id": None,
                "candidate_source_ids": [gap["catalog_source_id"]],
                "link_status": "disconnected",
                "scope_status": "candidate_in_report",
                "reason_code": "server_alias_lineage_gap",
                "severity": "blocker",
                "message": (
                    f"This report uses PostgreSQL server '{gap['report_server']}', but "
                    f"the dependency graph for {relation} was discovered under "
                    f"'{gap['catalog_server']}'. If these are aliases for one server, "
                    "make the Power BI connection and PGHOST use the same canonical host, "
                    "then run a Full Scan. Metronome did not merge them automatically."
                ),
                "recommended_action": "canonicalize_server",
                "executable": False,
            }
        )

    return {
        "included_count": included_count,
        "excluded_count": sum(
            1 for item in items if item.get("diagnostic_kind") == "flow"
        ) - included_count,
        # Retained for older clients. File-producing Flows are report-scoped
        # candidates rather than globally counted exclusions.
        "download_only_count": 0,
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
        (
            item["message"]
            if item.get("diagnostic_kind") == "lineage_gap"
            else f"Flow '{item['name']}': {item['message']}"
        )
        for item in flow_diagnostics.get("items", [])
        if item.get("severity") == "blocker"
    ]
