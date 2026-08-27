"""Exact PostgreSQL relation identities used by executable lineage.

The helpers in this module deliberately separate inspection from mutation.
Request/preview code can resolve an effective target without changing the
database, while scanner and startup code can explicitly persist a uniquely
resolved target in a controlled transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit


def normalize_server(value: str | None) -> str:
    """Return a stable host identity without altering database identifiers."""
    raw = (value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"postgresql://{raw}"
    parsed = urlsplit(candidate)
    host = parsed.hostname or raw.split("/", 1)[0].split(":", 1)[0]
    return host.rstrip(".").casefold()


def split_relation(value: str | None, default_schema: str | None = None) -> tuple[str, str] | None:
    """Split schema.relation while retaining quoted identifier spelling."""
    raw = (value or "").strip()
    if not raw:
        return None
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == '"':
            if quoted and index + 1 < len(raw) and raw[index + 1] == '"':
                current.append('"')
                index += 2
                continue
            quoted = not quoted
        elif char == "." and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current).strip())
    if any(not item for item in parts) or len(parts) > 2:
        return None
    if len(parts) == 1:
        schema = (default_schema or "public").strip()
        relation = parts[0]
    else:
        schema, relation = parts
    if not schema or not relation:
        return None
    return schema, relation


def postgres_identity_tuple(
    *, server: str | None, database: str | None, schema: str | None, relation: str | None
) -> tuple[str, str, str, str]:
    """Return the exact physical coordinate tuple stored for a PG relation."""
    return (
        normalize_server(server),
        (database or "").strip(),
        (schema or "").strip(),
        (relation or "").strip(),
    )


def _identity_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "source_id": int(row["source_id"]),
        "server": row["server_name"],
        "database": row["database_name"],
        "schema": row["schema_name"],
        "relation": row["relation_name"],
        "relation_kind": row["relation_kind"],
        "verified_at": row["verified_at"],
    }


def upsert_postgres_identity(
    db,
    *,
    source_id: int,
    server: str,
    database: str,
    schema: str,
    relation: str,
    relation_kind: str = "table",
    verified_at: str | None = None,
) -> dict:
    """Claim or refresh a source identity without ever changing coordinates.

    Historically this function used an unconditional ``ON CONFLICT`` update,
    allowing a fuzzy source match to reassign an existing source to a different
    physical table. Callers must now handle ``status == 'conflict'`` instead.
    """
    requested_tuple = postgres_identity_tuple(
        server=server,
        database=database,
        schema=schema,
        relation=relation,
    )
    verified_at = verified_at or datetime.now(timezone.utc).isoformat()
    relation_kind = (relation_kind or "table").strip() or "table"
    existing = db.execute(
        "SELECT * FROM source_postgres_identities WHERE source_id=?",
        (int(source_id),),
    ).fetchone()

    requested = {
        "source_id": int(source_id),
        "server": requested_tuple[0],
        "database": requested_tuple[1],
        "schema": requested_tuple[2],
        "relation": requested_tuple[3],
        "relation_kind": relation_kind,
        "verified_at": verified_at,
    }
    if existing is None:
        db.execute(
            """INSERT INTO source_postgres_identities
                   (source_id, server_name, database_name, schema_name, relation_name,
                    relation_kind, verified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (int(source_id), *requested_tuple, relation_kind, verified_at),
        )
        return {
            "status": "claimed",
            "source_id": int(source_id),
            "existing": None,
            "requested": requested,
        }

    existing_tuple = postgres_identity_tuple(
        server=existing["server_name"],
        database=existing["database_name"],
        schema=existing["schema_name"],
        relation=existing["relation_name"],
    )
    if existing_tuple != requested_tuple:
        return {
            "status": "conflict",
            "source_id": int(source_id),
            "existing": _identity_dict(existing),
            "requested": requested,
        }

    db.execute(
        """UPDATE source_postgres_identities
           SET relation_kind=?, verified_at=?
           WHERE source_id=?""",
        (relation_kind, verified_at, int(source_id)),
    )
    return {
        "status": "refreshed",
        "source_id": int(source_id),
        "existing": _identity_dict(existing),
        "requested": requested,
    }


def exact_identity_rows(
    db, *, server: str, database: str, schema: str, relation: str
) -> list:
    return db.execute(
        """SELECT spi.*, s.name AS source_name, COALESCE(s.archived, 0) AS archived
           FROM source_postgres_identities spi
           JOIN sources s ON s.id = spi.source_id
           WHERE spi.server_name = ? AND spi.database_name = ?
             AND spi.schema_name = ? AND spi.relation_name = ?
             AND COALESCE(s.archived, 0) = 0
           ORDER BY spi.source_id""",
        postgres_identity_tuple(
            server=server,
            database=database,
            schema=schema,
            relation=relation,
        ),
    ).fetchall()


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _flow_row(db, flow_or_id):
    if isinstance(flow_or_id, int):
        return db.execute(
            """SELECT id, sql_handoff_enabled, sql_database, sql_schema, sql_table,
                      sql_target_source_id
               FROM flows WHERE id=?""",
            (int(flow_or_id),),
        ).fetchone()
    return flow_or_id


def _resolution(
    *,
    status: str,
    reason_code: str | None,
    persisted_source_id: int | None,
    effective_source_id: int | None,
    matches: list[int],
    persisted_valid: bool,
    target: dict,
) -> dict:
    return {
        "status": status,
        "reason_code": reason_code,
        "persisted_source_id": persisted_source_id,
        "effective_source_id": effective_source_id,
        # Backward-compatible key used by the Flow serializer. It denotes the
        # exact effective target, never an invalid persisted target.
        "source_id": effective_source_id,
        "matches": matches,
        "persisted_valid": persisted_valid,
        "target": target,
    }


def inspect_flow_target(db, flow_or_id, *, server: str) -> dict:
    """Purely inspect a Flow's persisted and effective exact SQL target."""
    flow = _flow_row(db, flow_or_id)
    if flow is None:
        return _resolution(
            status="missing_flow",
            reason_code="missing_flow",
            persisted_source_id=None,
            effective_source_id=None,
            matches=[],
            persisted_valid=False,
            target={"server": normalize_server(server), "database": "", "schema": "", "relation": ""},
        )

    linked_value = _row_value(flow, "sql_target_source_id")
    persisted_source_id = int(linked_value) if linked_value is not None else None
    target_tuple = postgres_identity_tuple(
        server=server,
        database=_row_value(flow, "sql_database"),
        schema=_row_value(flow, "sql_schema"),
        relation=_row_value(flow, "sql_table"),
    )
    target = {
        "server": target_tuple[0],
        "database": target_tuple[1],
        "schema": target_tuple[2],
        "relation": target_tuple[3],
    }

    if not _row_value(flow, "sql_handoff_enabled", 0):
        return _resolution(
            status="disabled",
            reason_code="disabled",
            persisted_source_id=persisted_source_id,
            effective_source_id=None,
            matches=[],
            persisted_valid=False,
            target=target,
        )
    # The Flow owns database/schema/relation completeness.  The server comes
    # from configuration and may legitimately be the empty normalized value
    # in local/test installations; it still participates in exact matching.
    if not all(target_tuple[1:]):
        return _resolution(
            status="unresolved",
            reason_code="incomplete_target",
            persisted_source_id=persisted_source_id,
            effective_source_id=None,
            matches=[],
            persisted_valid=False,
            target=target,
        )

    matches = exact_identity_rows(
        db,
        server=target["server"],
        database=target["database"],
        schema=target["schema"],
        relation=target["relation"],
    )
    match_ids = [int(row["source_id"]) for row in matches]

    if persisted_source_id is not None:
        persisted = db.execute(
            """SELECT spi.*, COALESCE(s.archived, 0) AS archived
               FROM sources s
               LEFT JOIN source_postgres_identities spi ON spi.source_id=s.id
               WHERE s.id=?""",
            (persisted_source_id,),
        ).fetchone()
        if persisted is None or persisted["archived"] or persisted["source_id"] is None:
            invalid_status = "stale"
            reason_code = "stale_target_link"
        else:
            actual_tuple = postgres_identity_tuple(
                server=persisted["server_name"],
                database=persisted["database_name"],
                schema=persisted["schema_name"],
                relation=persisted["relation_name"],
            )
            if actual_tuple == target_tuple:
                return _resolution(
                    status="confirmed",
                    reason_code=None,
                    persisted_source_id=persisted_source_id,
                    effective_source_id=persisted_source_id,
                    matches=match_ids,
                    persisted_valid=True,
                    target=target,
                )
            invalid_status = "target_changed"
            reason_code = "target_changed"

        effective_source_id = match_ids[0] if len(match_ids) == 1 else None
        return _resolution(
            status=invalid_status,
            reason_code=reason_code,
            persisted_source_id=persisted_source_id,
            effective_source_id=effective_source_id,
            matches=match_ids,
            persisted_valid=False,
            target=target,
        )

    if len(match_ids) == 1:
        return _resolution(
            status="confirmed",
            reason_code=None,
            persisted_source_id=None,
            effective_source_id=match_ids[0],
            matches=match_ids,
            persisted_valid=False,
            target=target,
        )
    return _resolution(
        status="ambiguous" if match_ids else "unresolved",
        reason_code="ambiguous_target" if match_ids else "target_not_discovered",
        persisted_source_id=None,
        effective_source_id=None,
        matches=match_ids,
        persisted_valid=False,
        target=target,
    )


def reconcile_flow_target(db, flow_id: int, *, server: str) -> dict:
    """Persist a uniquely resolved exact target without clearing old evidence."""
    inspected = inspect_flow_target(db, int(flow_id), server=server)
    effective_source_id = inspected["effective_source_id"]
    if effective_source_id is not None:
        if inspected["persisted_source_id"] != effective_source_id:
            db.execute(
                """UPDATE flows
                   SET sql_target_source_id=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND (sql_target_source_id IS NULL OR sql_target_source_id<>?)""",
                (effective_source_id, int(flow_id), effective_source_id),
            )
        return {
            "status": "confirmed",
            "source_id": int(effective_source_id),
            "matches": [int(effective_source_id)],
        }
    return {
        "status": inspected["status"],
        "source_id": None,
        "matches": inspected["matches"],
    }


def reconcile_all_flow_targets(db, *, server: str) -> dict:
    """Idempotently reconcile every Flow after a complete identity batch."""
    counts = {
        "total": 0,
        "changed": 0,
        "confirmed": 0,
        "ambiguous": 0,
        "unresolved": 0,
        "stale": 0,
        "target_changed": 0,
        "disabled": 0,
    }
    flow_ids = [int(row["id"]) for row in db.execute("SELECT id FROM flows ORDER BY id").fetchall()]
    for flow_id in flow_ids:
        before = inspect_flow_target(db, flow_id, server=server)
        result = reconcile_flow_target(db, flow_id, server=server)
        counts["total"] += 1
        if (
            before["effective_source_id"] is not None
            and before["persisted_source_id"] != before["effective_source_id"]
        ):
            counts["changed"] += 1
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def flow_link_status(db, flow, *, server: str) -> dict:
    """Backward-compatible, read-only Flow-link inspection."""
    inspected = inspect_flow_target(db, flow, server=server)
    return {
        "status": inspected["status"],
        "source_id": inspected["effective_source_id"],
        "matches": inspected["matches"],
        "persisted_source_id": inspected["persisted_source_id"],
        "effective_source_id": inspected["effective_source_id"],
        "reason_code": inspected["reason_code"],
        "persisted_valid": inspected["persisted_valid"],
        "target": inspected["target"],
    }
