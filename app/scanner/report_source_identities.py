"""Safely repair report PostgreSQL identities from stored source expressions.

A focused PostgreSQL lineage recheck historically refreshed ``pg_depend`` but
did not reparse the Power BI source expression that anchors a report to that
catalog graph. This module performs that narrow repair without fuzzy matching
display names or overwriting an existing physical identity.

Deferred mode is intended for catalog scanners. It may prepare exact source
identities so a catalog snapshot can attach dependency edges, but it keeps each
``report_tables`` link unchanged until the caller explicitly finalizes the
matching server/database after a successful catalog apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import UPLOAD_PGHOST, UPLOAD_PGPORT
from app.database import get_db
from app.scanner.tmdl_parser import (
    _parse_m_expression,
    literal_postgres_connection,
)
from app.source_identity import (
    exact_identity_rows,
    normalize_server,
    postgres_identity_tuple,
    postgres_server_identity,
    split_relation,
    upsert_postgres_identity,
)


@dataclass(frozen=True)
class _ExactTarget:
    parsed: Any
    server: str
    database: str
    schema: str
    relation: str

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return postgres_identity_tuple(
            server=self.server,
            database=self.database,
            schema=self.schema,
            relation=self.relation,
        )


@dataclass(frozen=True)
class _PendingRelink:
    report_id: int
    report_table_id: int
    original_source_id: int | None
    target_source_id: int
    server: str
    database: str
    schema: str
    relation: str


class ReportIdentityReconciliation(dict):
    """JSON-safe public summary with deferred relinks held out-of-band."""

    def __init__(self, *args, pending_relinks: list[_PendingRelink] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending_relinks = list(pending_relinks or [])


def _identity_tuple(row) -> tuple[str, str, str, str] | None:
    if row is None or row["identity_source_id"] is None:
        return None
    return postgres_identity_tuple(
        server=row["server_name"],
        database=row["database_name"],
        schema=row["schema_name"],
        relation=row["relation_name"],
    )


def _unique_source_name(db, base_name: str, *, database: str, server: str) -> str:
    candidate = base_name
    if not db.execute("SELECT 1 FROM sources WHERE name=?", (candidate,)).fetchone():
        return candidate
    qualified = f"{base_name} [{database}@{server}]"
    candidate = qualified
    suffix = 2
    while db.execute("SELECT 1 FROM sources WHERE name=?", (candidate,)).fetchone():
        candidate = f"{qualified} #{suffix}"
        suffix += 1
    return candidate


def _create_exact_source(db, target: _ExactTarget, *, now: str) -> int:
    """Create one exact source without moving any existing report links."""
    source_name = _unique_source_name(
        db,
        target.parsed.display_name,
        database=target.database,
        server=target.server,
    )
    cursor = db.execute(
        """INSERT INTO sources
               (name, type, connection_info, source_query, discovered_by,
                created_at, updated_at)
           VALUES (?, 'postgresql', ?, ?, 'scan', ?, ?)""",
        (
            source_name,
            target.parsed.connection_info,
            target.parsed.raw_expression,
            now,
            now,
        ),
    )
    source_id = int(cursor.lastrowid)
    claim = upsert_postgres_identity(
        db,
        source_id=source_id,
        server=target.server,
        database=target.database,
        schema=target.schema,
        relation=target.relation,
        relation_kind="table",
        verified_at=now,
    )
    if claim["status"] == "conflict":  # pragma: no cover - new IDs are unowned
        raise RuntimeError("A newly created report source could not claim its identity.")
    return source_id


def _parse_exact_target(expression: str) -> tuple[_ExactTarget | None, str | None]:
    raw = str(expression or "")
    if "PostgreSQL.Database" not in raw:
        return None, "not_postgresql"
    literal_connection = literal_postgres_connection(raw)
    if literal_connection is None:
        return None, "nonliteral_postgres_connection"
    parsed = _parse_m_expression(raw)
    if not parsed.postgres_single_native_query:
        return None, "multiple_native_postgres_queries"
    if not parsed.postgres_native_query_exact:
        return None, "nonliteral_native_postgres_query"
    if parsed.source_type != "postgresql" or not parsed.sql_table:
        return None, "unresolved_postgres_relation"
    if not parsed.postgres_conditional_output_exact:
        return None, "conditional_postgres_output"
    # A bare relation in native SQL is resolved by PostgreSQL's role-specific
    # search_path, not necessarily ``public``. The local catalog cannot safely
    # guess that runtime binding. Explicit Schema/Item navigation remains
    # eligible because it comes from Power BI's resolved navigator identity.
    if not parsed.postgres_relation_exact:
        return (
            None,
            "unqualified_native_postgres_relation"
            if parsed.sql_query is not None
            else "unqualified_postgres_navigation_relation",
        )
    parts = split_relation(parsed.sql_table)
    if parts is None:
        return None, "unresolved_postgres_relation"
    server, database = literal_connection
    if not normalize_server(server):
        return None, "invalid_postgres_endpoint"
    # Defend against future parser changes: both independently decoded values
    # must identify the same literal connection.
    if (parsed.server or "").strip() != server or (parsed.database or "").strip() != database:
        return None, "nonliteral_postgres_connection"
    schema, relation = parts
    return (
        _ExactTarget(
            parsed=parsed,
            server=server,
            database=database,
            schema=schema,
            relation=relation,
        ),
        None,
    )


def _issue(result: dict, row, reason_code: str) -> None:
    """Append bounded identifiers only; never include an M expression."""
    result["issues"].append(
        {
            "report_table_id": int(row["report_table_id"]),
            "source_id": int(row["source_id"]) if row["source_id"] is not None else None,
            "reason_code": reason_code,
        }
    )


def _reference_expression(row) -> str:
    return str(row["source_expression"] or row["source_query"] or "")


def _source_can_claim_in_place(db, source_id: int, target: _ExactTarget) -> bool:
    """Return whether assigning this shared source is globally consistent."""
    report_refs = db.execute(
        """SELECT rt.source_expression, s.source_query
             FROM report_tables rt
             LEFT JOIN sources s ON s.id=rt.source_id
            WHERE rt.source_id=?
            ORDER BY rt.id""",
        (int(source_id),),
    ).fetchall()
    if not report_refs:
        return False
    shared = len(report_refs) > 1
    for reference in report_refs:
        row_expression = str(reference["source_expression"] or "").strip()
        # sources.source_query is only one legacy representative expression.
        # It cannot prove that another report sharing the source agrees. A
        # sole legacy reference may use it; shared rows require their own M.
        if shared and not row_expression:
            return False
        expression = row_expression or str(reference["source_query"] or "")
        parsed, _reason = _parse_exact_target(expression)
        if parsed is None or parsed.identity != target.identity:
            return False

    flow_refs = db.execute(
        """SELECT sql_database, sql_schema, sql_table
             FROM flows
            WHERE sql_target_source_id=?
            ORDER BY id""",
        (int(source_id),),
    ).fetchall()
    for flow in flow_refs:
        flow_identity = postgres_identity_tuple(
            server=postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT),
            database=flow["sql_database"],
            schema=flow["sql_schema"],
            relation=flow["sql_table"],
        )
        if flow_identity != target.identity:
            return False
    return True


def _source_condition(original_source_id: int | None) -> tuple[str, tuple]:
    if original_source_id is None:
        return "source_id IS NULL", ()
    return "source_id=?", (int(original_source_id),)


def _apply_relink(db, pending: _PendingRelink) -> bool:
    """Compare-and-set one report row without overwriting concurrent edits."""
    identity = db.execute(
        """SELECT spi.source_id
             FROM source_postgres_identities spi
             JOIN sources s ON s.id=spi.source_id
            WHERE spi.source_id=? AND spi.server_name=? AND spi.database_name=?
              AND spi.schema_name=? AND spi.relation_name=?
              AND COALESCE(s.archived, 0)=0""",
        (
            int(pending.target_source_id),
            normalize_server(pending.server),
            pending.database,
            pending.schema,
            pending.relation,
        ),
    ).fetchone()
    if identity is None:
        return False
    condition, values = _source_condition(pending.original_source_id)
    cursor = db.execute(
        f"""UPDATE report_tables SET source_id=?
              WHERE id=? AND report_id=? AND {condition}""",
        (
            int(pending.target_source_id),
            int(pending.report_table_id),
            int(pending.report_id),
            *values,
        ),
    )
    return bool(cursor.rowcount)


def _public_result(report_id: int | None, *, defer_relinks: bool) -> ReportIdentityReconciliation:
    return ReportIdentityReconciliation(
        {
            "status": "not_requested" if report_id is None else "completed",
            "report_id": int(report_id) if report_id is not None else None,
            "deferred": bool(defer_relinks),
            "rows_examined": 0,
            "parsed": 0,
            "skipped": 0,
            "confirmed": 0,
            "claimed": 0,
            "created": 0,
            "relinked": 0,
            "pending_relinks": 0,
            "not_applied": 0,
            "unconfigured_catalog_targets": 0,
            "ambiguous": 0,
            "unresolved": 0,
            "catalog_targets": [],
            "issues": [],
        }
    )


def _record_catalog_target(
    result: ReportIdentityReconciliation,
    target: _ExactTarget,
) -> None:
    """Expose only the exact catalog coordinate needed by the scanner."""
    item = {
        "server": normalize_server(target.server),
        "database": target.database,
    }
    if item not in result["catalog_targets"]:
        result["catalog_targets"].append(item)


def _refresh_status(result: ReportIdentityReconciliation, *, final: bool = False) -> None:
    if result.get("status") in {"not_requested", "missing_report"}:
        return
    if result._pending_relinks and not final:
        result["status"] = "pending"
    elif (
        result["ambiguous"]
        or result["unresolved"]
        or result["not_applied"]
        or result.get("unconfigured_catalog_targets")
    ):
        result["status"] = "completed_with_warnings"
    else:
        result["status"] = "completed"
    result["pending_relinks"] = len(result._pending_relinks)


def reconcile_report_postgres_identities(
    report_id: int | None,
    *,
    defer_relinks: bool = False,
) -> ReportIdentityReconciliation:
    """Repair exact PostgreSQL anchors for one report.

    Default behavior remains immediate for direct callers. With
    ``defer_relinks=True``, source identities may be prepared but report-table
    links are retained internally until the exact catalog server/database is
    finalized after a successful snapshot apply.
    """
    result = _public_result(report_id, defer_relinks=defer_relinks)
    if report_id is None:
        return result

    now = datetime.now(timezone.utc).isoformat()
    pending_relinks: list[_PendingRelink] = []
    with get_db() as db:
        report = db.execute("SELECT id FROM reports WHERE id=?", (int(report_id),)).fetchone()
        if report is None:
            result["status"] = "missing_report"
            return result
        rows = db.execute(
            """SELECT rt.id AS report_table_id, rt.source_id, rt.source_expression,
                      s.id AS existing_source_id, s.name AS source_name,
                      s.type AS source_type, s.source_query,
                      (SELECT COUNT(*) FROM report_tables shared_rt
                        WHERE shared_rt.source_id=rt.source_id) AS source_ref_count,
                      COALESCE(s.archived, 0) AS archived,
                      spi.source_id AS identity_source_id, spi.server_name,
                      spi.database_name, spi.schema_name, spi.relation_name
                 FROM report_tables rt
                 LEFT JOIN sources s ON s.id=rt.source_id
                 LEFT JOIN source_postgres_identities spi ON spi.source_id=s.id
                WHERE rt.report_id=?
                ORDER BY rt.id""",
            (int(report_id),),
        ).fetchall()

        result["rows_examined"] = len(rows)
        for row in rows:
            own_expression = str(row["source_expression"] or "").strip()
            if not own_expression and int(row["source_ref_count"] or 0) > 1:
                target, reason = None, "missing_report_source_expression"
            else:
                target, reason = _parse_exact_target(_reference_expression(row))
            if target is None:
                source_says_postgres = str(row["source_type"] or "").casefold() == "postgresql"
                if reason == "not_postgresql" and not source_says_postgres:
                    result["skipped"] += 1
                    continue
                result["unresolved"] += 1
                _issue(result, row, reason or "unresolved_postgres_source")
                continue

            result["parsed"] += 1
            _record_catalog_target(result, target)
            current = _identity_tuple(row)
            if current == target.identity and not row["archived"]:
                result["confirmed"] += 1
                continue

            matches = exact_identity_rows(
                db,
                server=target.server,
                database=target.database,
                schema=target.schema,
                relation=target.relation,
            )
            match_ids = sorted({int(match["source_id"]) for match in matches})
            if len(match_ids) > 1:
                result["ambiguous"] += 1
                _issue(result, row, "ambiguous_exact_identity")
                continue

            original_source_id = int(row["source_id"]) if row["source_id"] is not None else None
            if len(match_ids) == 1:
                target_source_id = match_ids[0]
                if original_source_id == target_source_id:
                    result["confirmed"] += 1
                    continue
            elif (
                row["existing_source_id"] is not None
                and current is None
                and not row["archived"]
                and str(row["source_type"] or "").casefold()
                in {"", "unknown", "postgresql"}
                and _source_can_claim_in_place(db, int(row["existing_source_id"]), target)
            ):
                claim = upsert_postgres_identity(
                    db,
                    source_id=int(row["existing_source_id"]),
                    server=target.server,
                    database=target.database,
                    schema=target.schema,
                    relation=target.relation,
                    relation_kind="table",
                    verified_at=now,
                )
                if claim["status"] != "conflict":
                    db.execute(
                        """UPDATE sources
                              SET type='postgresql', connection_info=?,
                                  source_query=?, updated_at=?
                            WHERE id=?""",
                        (
                            target.parsed.connection_info,
                            target.parsed.raw_expression,
                            now,
                            int(row["existing_source_id"]),
                        ),
                    )
                    result["claimed"] += 1
                    continue
                target_source_id = _create_exact_source(db, target, now=now)
                result["created"] += 1
            else:
                target_source_id = _create_exact_source(db, target, now=now)
                result["created"] += 1

            pending = _PendingRelink(
                report_id=int(report_id),
                report_table_id=int(row["report_table_id"]),
                original_source_id=original_source_id,
                target_source_id=int(target_source_id),
                server=target.server,
                database=target.database,
                schema=target.schema,
                relation=target.relation,
            )
            if defer_relinks:
                pending_relinks.append(pending)
            elif _apply_relink(db, pending):
                result["relinked"] += 1
            else:  # pragma: no cover - scanner serialization makes this exceptional
                result["not_applied"] += 1
                _issue(result, row, "report_table_changed")

    result._pending_relinks = pending_relinks
    _refresh_status(result)
    return result


def pending_report_postgres_identity_target_source_ids(
    reconciliation: ReportIdentityReconciliation,
    *,
    server: str,
    database: str,
) -> tuple[int, ...]:
    """Return pending target IDs that cleanup must protect for one catalog.

    Deferred relink details remain private.  The catalog applier only receives
    the bounded source IDs needed to avoid deleting a target between a
    successful snapshot apply and its exact server/database finalization.
    """
    if not isinstance(reconciliation, ReportIdentityReconciliation):
        raise TypeError("Expected ReportIdentityReconciliation from deferred repair.")
    wanted_server = normalize_server(server)
    wanted_database = str(database or "").strip()
    return tuple(
        sorted(
            {
                int(item.target_source_id)
                for item in reconciliation._pending_relinks
                if normalize_server(item.server) == wanted_server
                and item.database == wanted_database
            }
        )
    )


def finalize_report_postgres_identity_relinks(
    reconciliation: ReportIdentityReconciliation,
    *,
    server: str,
    database: str,
) -> ReportIdentityReconciliation:
    """Apply deferred links for one successfully committed catalog coordinate."""
    if not isinstance(reconciliation, ReportIdentityReconciliation):
        raise TypeError("Expected ReportIdentityReconciliation from deferred repair.")
    wanted_server = normalize_server(server)
    wanted_database = str(database or "").strip()
    matching = [
        item
        for item in reconciliation._pending_relinks
        if normalize_server(item.server) == wanted_server and item.database == wanted_database
    ]
    remaining = [item for item in reconciliation._pending_relinks if item not in matching]
    if matching:
        with get_db() as db:
            for pending in matching:
                if _apply_relink(db, pending):
                    reconciliation["relinked"] += 1
                else:
                    reconciliation["not_applied"] += 1
                    reconciliation["issues"].append(
                        {
                            "report_table_id": pending.report_table_id,
                            "source_id": pending.original_source_id,
                            "reason_code": "deferred_relink_stale",
                        }
                    )
    reconciliation._pending_relinks = remaining
    _refresh_status(reconciliation)
    return reconciliation


def complete_report_postgres_identity_reconciliation(
    reconciliation: ReportIdentityReconciliation,
) -> ReportIdentityReconciliation:
    """Finish deferred repair and safely abandon links without catalog success."""
    if not isinstance(reconciliation, ReportIdentityReconciliation):
        raise TypeError("Expected ReportIdentityReconciliation from deferred repair.")
    for pending in reconciliation._pending_relinks:
        reconciliation["not_applied"] += 1
        reconciliation["issues"].append(
            {
                "report_table_id": pending.report_table_id,
                "source_id": pending.original_source_id,
                "reason_code": "catalog_not_completed",
            }
        )
    reconciliation._pending_relinks = []
    reconciliation["deferred"] = False
    _refresh_status(reconciliation, final=True)
    return reconciliation
