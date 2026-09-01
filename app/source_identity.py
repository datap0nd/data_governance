"""Deterministic source identities used by Flow lineage.

The helpers in this module deliberately separate inspection from mutation.
Request/preview code can resolve an effective target without changing the
database, while scanner and startup code can explicitly persist a uniquely
resolved target in a controlled transaction.
"""

from __future__ import annotations

import ntpath
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit


def _postgres_server_parts(value: str | None) -> tuple[str, int | None]:
    raw = (value or "").strip()
    if not raw:
        return "", None
    # ``urlsplit`` requires IPv6 literals to be bracketed.  libpq also accepts
    # a bare address in PGHOST, so preserve that form as one host rather than
    # misreading its colons as a hostname/port separator.  A port paired with
    # IPv6 must use brackets (or the separate ``port`` argument), because an
    # unbracketed trailing number is indistinguishable from the address itself.
    if "://" not in raw and not raw.startswith("[") and raw.count(":") >= 2:
        return raw.rstrip(".").casefold(), None
    candidate = raw if "://" in raw else f"postgresql://{raw}"
    parsed = urlsplit(candidate)
    host = parsed.hostname or raw.split("/", 1)[0].split(":", 1)[0]
    try:
        port = parsed.port
    except ValueError:
        # A malformed or out-of-range explicit port is not equivalent to the
        # default PostgreSQL endpoint. Returning no identity prevents a typo
        # from attaching lineage to a different physical cluster.
        return "", None
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if authority.endswith(":"):
        return "", None
    if port is not None and not 1 <= port <= 65535:
        return "", None
    return host.rstrip(".").casefold(), port


def postgres_server_identity(
    value: str | None,
    port: str | int | None = None,
) -> str:
    """Return a stable physical PostgreSQL endpoint identity.

    The default PostgreSQL port remains equivalent to an omitted port for
    compatibility with existing identities. Non-default ports are retained so
    two clusters on the same hostname can never be merged into false lineage.
    An explicit port in ``value`` takes precedence over the separate setting.
    """
    host, explicit_port = _postgres_server_parts(value)
    if not host:
        return ""
    selected_port = explicit_port
    if selected_port is None and port not in (None, ""):
        try:
            selected_port = int(port)
        except (TypeError, ValueError):
            return ""
    if selected_port is not None and not 1 <= selected_port <= 65535:
        return ""
    formatted_host = f"[{host}]" if ":" in host else host
    if selected_port in (None, 5432):
        return formatted_host
    return f"{formatted_host}:{selected_port}"


def normalize_server(value: str | None) -> str:
    """Return a stable PostgreSQL endpoint without altering DB identifiers."""
    return postgres_server_identity(value)


def normalize_file_path(value: str | None) -> str:
    """Return a comparison key for a Windows/UNC file path.

    This deliberately does not resolve the path or touch the filesystem.  A
    Flow runs on the BI desktop and the scanner may run under a different
    account, so ``Path.resolve`` would make an otherwise identical configured
    path machine-dependent.  Windows paths are case-insensitive and accept
    both slash styles; those are the only normalizations performed here.
    """
    raw = (value or "").strip().strip('"')
    if not raw:
        return ""
    raw = raw.replace("/", "\\")
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return ntpath.normpath(raw).rstrip("\\").casefold()


def static_flow_filename(value: str | None) -> str | None:
    """Return a literal output basename, or ``None`` for dynamic templates."""
    raw = (value or "").strip().strip('"')
    if not raw or re.search(r"\{[^{}]+\}", raw):
        return None
    name = ntpath.basename(raw.replace("/", "\\"))
    if not name or name in {".", ".."}:
        return None
    return name


def file_flow_target(flow) -> dict:
    """Describe the deterministic file output configured for a Flow."""
    filename = static_flow_filename(_row_value(flow, "filename_template"))
    folder = (_row_value(flow, "target_folder") or "").strip()
    path = ntpath.join(folder, filename) if folder and filename else ""
    return {
        "kind": "file",
        "folder": folder,
        "filename": filename,
        "path": path or None,
        "normalized_path": normalize_file_path(path),
    }


def inspect_file_flow_target(
    db,
    flow,
    report_source_ids,
    *,
    report_sources=None,
) -> dict:
    """Find conservative file-output evidence in one report closure.

    An exact configured path wins.  Older scanner rows are keyed by basename
    and often point through per-run folders, so a literal filename may be used
    only when that basename identifies exactly one active file source in the
    entire registry. Even an exact result is presentation evidence, not
    permission to execute the Flow in a Pipeline: file-output orchestration is
    deliberately outside the executable Pipeline contract, including for a
    direct-output Flow. Partial, fuzzy, and dynamic-template matches are
    rejected entirely.
    """
    target = file_flow_target(flow)
    closure = {
        int(source_id) for source_id in report_source_ids if source_id is not None
    }
    if not target["filename"]:
        return _resolution(
            status="unresolved",
            reason_code="dynamic_file_target",
            persisted_source_id=None,
            effective_source_id=None,
            matches=[],
            persisted_valid=False,
            target=target,
            match_strategy=None,
        )

    # Basename uniqueness must be global, not merely unique inside one report,
    # or two physical files could silently become one lineage candidate.
    # Re-read the small registry even when report rows were supplied by the
    # lineage endpoint.
    rows = db.execute(
        """SELECT id, name, type, connection_info
           FROM sources
           WHERE COALESCE(archived, 0)=0
             AND lower(COALESCE(type, '')) IN ('csv','excel','file')
           ORDER BY id"""
    ).fetchall()
    all_sources = [dict(row) for row in rows]

    wanted_path = target["normalized_path"]
    exact_path_ids = sorted({
        int(source["id"])
        for source in all_sources
        if wanted_path
        and normalize_file_path(source.get("connection_info")) == wanted_path
    })
    exact_in_report = [source_id for source_id in exact_path_ids if source_id in closure]
    if len(exact_path_ids) == 1 and len(exact_in_report) == 1:
        return _resolution(
            status="confirmed",
            reason_code=None,
            persisted_source_id=None,
            effective_source_id=exact_in_report[0],
            matches=exact_path_ids,
            persisted_valid=False,
            target=target,
            match_strategy="exact_path",
        )
    if exact_in_report:
        return _resolution(
            status="ambiguous",
            reason_code="ambiguous_file_target",
            persisted_source_id=None,
            effective_source_id=None,
            matches=exact_in_report,
            persisted_valid=False,
            target=target,
            match_strategy="exact_path",
        )

    wanted_name = target["filename"].casefold()
    basename_ids = sorted({
        int(source["id"])
        for source in all_sources
        if ntpath.basename(
            (source.get("connection_info") or source.get("name") or "")
            .replace("/", "\\")
            .rstrip("\\")
        ).casefold() == wanted_name
    })
    basename_in_report = [source_id for source_id in basename_ids if source_id in closure]
    if len(basename_ids) == 1 and len(basename_in_report) == 1:
        return _resolution(
            status="confirmed",
            reason_code=None,
            persisted_source_id=None,
            effective_source_id=basename_in_report[0],
            matches=basename_ids,
            persisted_valid=False,
            target=target,
            match_strategy="unique_basename",
        )
    if basename_in_report:
        return _resolution(
            status="ambiguous",
            reason_code="ambiguous_file_target",
            persisted_source_id=None,
            effective_source_id=None,
            matches=basename_in_report,
            persisted_valid=False,
            target=target,
            match_strategy="basename",
        )
    return _resolution(
        status="unresolved",
        reason_code="file_target_not_in_report",
        persisted_source_id=None,
        effective_source_id=None,
        matches=[],
        persisted_valid=False,
        target=target,
        match_strategy=None,
    )


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
    if quoted:
        return None
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
    preserve_existing_relation_kind: bool = False,
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

    effective_relation_kind = (
        str(existing["relation_kind"] or "table")
        if preserve_existing_relation_kind
        else relation_kind
    )
    db.execute(
        """UPDATE source_postgres_identities
           SET relation_kind=?, verified_at=?
           WHERE source_id=?""",
        (effective_relation_kind, verified_at, int(source_id)),
    )
    return {
        "status": "refreshed",
        "source_id": int(source_id),
        "existing": _identity_dict(existing),
        "requested": requested,
        "relation_kind": effective_relation_kind,
        "relation_kind_preserved": bool(
            preserve_existing_relation_kind
            and effective_relation_kind != relation_kind
        ),
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
    match_strategy: str | None = None,
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
        "match_strategy": match_strategy,
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sources)").fetchall()}
            if "freshness_mode" in columns:
                from app.freshness_inheritance import reconcile_source
                for source_id in {inspected["persisted_source_id"], effective_source_id} - {None}:
                    reconcile_source(db, int(source_id))
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
    columns = {row["name"] for row in db.execute("PRAGMA table_info(sources)").fetchall()}
    if "freshness_mode" in columns:
        from app.freshness_inheritance import reconcile_all_sources
        reconcile_all_sources(db)
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
