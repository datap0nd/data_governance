"""Exact PostgreSQL relation identities used by executable lineage."""

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
) -> None:
    verified_at = verified_at or datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO source_postgres_identities
               (source_id, server_name, database_name, schema_name, relation_name,
                relation_kind, verified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
               server_name=excluded.server_name,
               database_name=excluded.database_name,
               schema_name=excluded.schema_name,
               relation_name=excluded.relation_name,
               relation_kind=excluded.relation_kind,
               verified_at=excluded.verified_at""",
        (
            int(source_id),
            normalize_server(server),
            (database or "").strip(),
            schema,
            relation,
            relation_kind,
            verified_at,
        ),
    )


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
        (
            normalize_server(server),
            (database or "").strip(),
            schema,
            relation,
        ),
    ).fetchall()


def reconcile_flow_target(db, flow_id: int, *, server: str) -> dict:
    """Set a Flow link only when its configured SQL target resolves once."""
    flow = db.execute(
        """SELECT id, sql_handoff_enabled, sql_database, sql_schema, sql_table,
                  sql_target_source_id
           FROM flows WHERE id = ?""",
        (flow_id,),
    ).fetchone()
    if not flow:
        return {"status": "missing_flow", "source_id": None, "matches": []}
    if not flow["sql_handoff_enabled"]:
        db.execute("UPDATE flows SET sql_target_source_id=NULL WHERE id=?", (flow_id,))
        return {"status": "disabled", "source_id": None, "matches": []}

    database = (flow["sql_database"] or "").strip()
    schema = (flow["sql_schema"] or "").strip()
    relation = (flow["sql_table"] or "").strip()
    if not server or not database or not schema or not relation:
        db.execute("UPDATE flows SET sql_target_source_id=NULL WHERE id=?", (flow_id,))
        return {"status": "unresolved", "source_id": None, "matches": []}

    matches = exact_identity_rows(
        db,
        server=server,
        database=database,
        schema=schema,
        relation=relation,
    )
    if len(matches) == 1:
        source_id = int(matches[0]["source_id"])
        db.execute(
            "UPDATE flows SET sql_target_source_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (source_id, flow_id),
        )
        return {"status": "confirmed", "source_id": source_id, "matches": [source_id]}

    db.execute("UPDATE flows SET sql_target_source_id=NULL WHERE id=?", (flow_id,))
    return {
        "status": "ambiguous" if matches else "unresolved",
        "source_id": None,
        "matches": [int(row["source_id"]) for row in matches],
    }


def flow_link_status(db, flow, *, server: str) -> dict:
    if not flow["sql_handoff_enabled"]:
        return {"status": "disabled", "source_id": None, "matches": []}
    linked_id = flow["sql_target_source_id"]
    if linked_id is None:
        return reconcile_flow_target(db, int(flow["id"]), server=server)
    row = db.execute(
        """SELECT spi.*, COALESCE(s.archived, 0) AS archived
           FROM source_postgres_identities spi
           JOIN sources s ON s.id=spi.source_id
           WHERE spi.source_id=?""",
        (linked_id,),
    ).fetchone()
    if not row or row["archived"]:
        db.execute("UPDATE flows SET sql_target_source_id=NULL WHERE id=?", (flow["id"],))
        return {"status": "stale", "source_id": None, "matches": []}
    expected = (
        normalize_server(server),
        (flow["sql_database"] or "").strip(),
        (flow["sql_schema"] or "").strip(),
        (flow["sql_table"] or "").strip(),
    )
    actual = (
        row["server_name"], row["database_name"], row["schema_name"], row["relation_name"]
    )
    if actual != expected:
        reconciled = reconcile_flow_target(db, int(flow["id"]), server=server)
        if reconciled["status"] == "confirmed":
            return reconciled
        return {**reconciled, "status": "target_changed"}
    return {"status": "confirmed", "source_id": int(linked_id), "matches": [int(linked_id)]}
