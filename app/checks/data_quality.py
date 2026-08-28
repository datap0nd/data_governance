"""Scheduled read-only data-quality checks for governed sources."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.config import PGHOST, PGPORT, UPLOAD_PGHOST, UPLOAD_PGPORT
from app.database import get_db
from app.scanner.prober import _get_flow_pg_connection, _get_pg_connection, _source_alert_owner
from app.source_identity import normalize_server, postgres_server_identity

logger = logging.getLogger(__name__)

CHECK_TYPES = {
    "row_count_range": {
        "label": "Row count range",
        "description": "Fail when the latest probe row count is below or above a configured limit.",
        "fields": ["min_rows", "max_rows"],
        "uses_postgres": False,
    },
    "row_count_change": {
        "label": "Row count change",
        "description": "Fail when the latest row count changes too far from the previous probe.",
        "fields": ["max_drop_pct", "max_increase_pct"],
        "uses_postgres": False,
    },
    "null_rate": {
        "label": "Null rate",
        "description": "Fail when a PostgreSQL column exceeds the allowed null percentage.",
        "fields": ["column", "max_null_pct"],
        "uses_postgres": True,
    },
    "duplicate_key": {
        "label": "Duplicate key",
        "description": "Fail when one or more PostgreSQL key combinations are duplicated.",
        "fields": ["columns", "max_duplicate_rows"],
        "uses_postgres": True,
    },
    "value_range": {
        "label": "Value range",
        "description": "Fail when PostgreSQL values fall below or above configured bounds.",
        "fields": ["column", "min_value", "max_value"],
        "uses_postgres": True,
    },
}

SEVERITIES = {"critical", "warning", "info"}


class CheckDefinitionError(ValueError):
    pass


def _number(config: dict, key: str, *, required: bool = False) -> float | None:
    value = config.get(key)
    if value is None or value == "":
        if required:
            raise CheckDefinitionError(f"{key} is required")
        return None
    if isinstance(value, bool):
        raise CheckDefinitionError(f"{key} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CheckDefinitionError(f"{key} must be a number") from exc


def _identifier(value, key: str) -> str:
    clean = str(value or "").strip()
    if not clean or "\x00" in clean or len(clean) > 128:
        raise CheckDefinitionError(f"{key} must be a valid column name")
    return clean


def validate_config(check_type: str, config: dict | None) -> dict:
    """Normalize and validate a check configuration."""
    if check_type not in CHECK_TYPES:
        raise CheckDefinitionError(f"Unsupported check type: {check_type}")
    raw = dict(config or {})

    if check_type == "row_count_range":
        minimum = _number(raw, "min_rows")
        maximum = _number(raw, "max_rows")
        if minimum is None and maximum is None:
            raise CheckDefinitionError("Set min_rows, max_rows, or both")
        if minimum is not None and minimum < 0:
            raise CheckDefinitionError("min_rows cannot be negative")
        if maximum is not None and maximum < 0:
            raise CheckDefinitionError("max_rows cannot be negative")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise CheckDefinitionError("min_rows cannot exceed max_rows")
        return {"min_rows": minimum, "max_rows": maximum}

    if check_type == "row_count_change":
        drop = _number(raw, "max_drop_pct")
        increase = _number(raw, "max_increase_pct")
        if drop is None and increase is None:
            raise CheckDefinitionError("Set max_drop_pct, max_increase_pct, or both")
        if drop is not None and not 0 <= drop <= 100:
            raise CheckDefinitionError("max_drop_pct must be between 0 and 100")
        if increase is not None and increase < 0:
            raise CheckDefinitionError("max_increase_pct cannot be negative")
        return {"max_drop_pct": drop, "max_increase_pct": increase}

    if check_type == "null_rate":
        maximum = _number(raw, "max_null_pct", required=True)
        if not 0 <= maximum <= 100:
            raise CheckDefinitionError("max_null_pct must be between 0 and 100")
        return {"column": _identifier(raw.get("column"), "column"), "max_null_pct": maximum}

    if check_type == "duplicate_key":
        columns = raw.get("columns")
        if isinstance(columns, str):
            columns = [part.strip() for part in columns.split(",") if part.strip()]
        if not isinstance(columns, list) or not columns:
            raise CheckDefinitionError("columns must contain at least one column")
        clean_columns = [_identifier(column, "columns") for column in columns]
        if len(set(clean_columns)) != len(clean_columns):
            raise CheckDefinitionError("columns cannot contain duplicates")
        maximum = _number(raw, "max_duplicate_rows")
        if maximum is None:
            maximum = 0
        if maximum < 0:
            raise CheckDefinitionError("max_duplicate_rows cannot be negative")
        return {"columns": clean_columns, "max_duplicate_rows": maximum}

    column = _identifier(raw.get("column"), "column")
    minimum = _number(raw, "min_value")
    maximum = _number(raw, "max_value")
    if minimum is None and maximum is None:
        raise CheckDefinitionError("Set min_value, max_value, or both")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise CheckDefinitionError("min_value cannot exceed max_value")
    return {"column": column, "min_value": minimum, "max_value": maximum}


def _row_counts(db, source_id: int) -> list[int]:
    rows = db.execute(
        """SELECT row_count FROM source_probes
           WHERE source_id = ? AND row_count IS NOT NULL
           ORDER BY probed_at DESC, id DESC LIMIT 2""",
        (source_id,),
    ).fetchall()
    return [int(row["row_count"]) for row in rows]


def _run_probe_check(db, source_id: int, check_type: str, config: dict) -> tuple[str, float | None, str]:
    counts = _row_counts(db, source_id)
    if not counts:
        return "skipped", None, "No probe row count is available yet"
    current = counts[0]

    if check_type == "row_count_range":
        minimum = config.get("min_rows")
        maximum = config.get("max_rows")
        failed = (minimum is not None and current < minimum) or (maximum is not None and current > maximum)
        bounds = []
        if minimum is not None:
            bounds.append(f"minimum {minimum:,.0f}")
        if maximum is not None:
            bounds.append(f"maximum {maximum:,.0f}")
        return ("fail" if failed else "pass", float(current), f"Latest row count is {current:,}; " + ", ".join(bounds))

    if len(counts) < 2:
        return "skipped", float(current), "A previous probe row count is required"
    previous = counts[1]
    if previous == 0:
        if current == 0:
            change_pct = 0.0
        else:
            return "skipped", float(current), "Previous row count was zero, so percentage change is undefined"
    else:
        change_pct = (current - previous) / previous * 100
    drop = config.get("max_drop_pct")
    increase = config.get("max_increase_pct")
    failed = (drop is not None and change_pct < -drop) or (increase is not None and change_pct > increase)
    return (
        "fail" if failed else "pass",
        round(change_pct, 4),
        f"Row count changed {change_pct:+.1f}% from {previous:,} to {current:,}",
    )


def _postgres_check_connection(source: dict, connections: dict):
    """Return a connection for one exact PostgreSQL identity, or an error.

    Quality checks must never infer executable coordinates from source display
    text.  A missing identity, an endpoint without configured read-only
    credentials, or a failed exact connection therefore fails closed.
    """
    server = normalize_server(source.get("server_name"))
    database = str(source.get("database_name") or "").strip()
    schema = str(source.get("schema_name") or "").strip()
    relation = str(source.get("relation_name") or "").strip()
    if not server or not database or not schema or not relation:
        return None, "Structured PostgreSQL identity is unavailable; no query was run"

    primary_server = postgres_server_identity(PGHOST, PGPORT)
    flow_server = postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT)
    if primary_server and server == primary_server:
        profile = "primary"
    elif flow_server and server == flow_server:
        profile = "flow"
    else:
        return None, "PostgreSQL endpoint is not configured for exact read-only checks; no query was run"

    key = (profile, server, database)
    if key not in connections:
        if profile == "primary":
            connection = _get_pg_connection(database=database)
            # Preserve the scanner's credential policy when Flow and the
            # primary catalogue point at the same physical endpoint.
            if connection is None and flow_server and server == flow_server:
                connection = _get_flow_pg_connection(database=database)
        else:
            connection = _get_flow_pg_connection(database=database)
        connections[key] = connection

    connection = connections[key]
    if connection is None:
        return None, "Read-only credentials failed for this exact PostgreSQL endpoint/database; no query was run"
    return connection, None


def _run_postgres_check(
    pg_conn,
    schema: str,
    relation: str,
    check_type: str,
    config: dict,
) -> tuple[str, float | None, str]:
    if pg_conn is None:
        return "error", None, "PostgreSQL read-only credentials are not configured or the connection failed"

    from psycopg2 import sql

    table_ref = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(relation))
    cursor = pg_conn.cursor()

    if check_type == "null_rate":
        column = sql.Identifier(config["column"])
        query = sql.SQL(
            "SELECT COUNT(*)::bigint, COUNT(*) FILTER (WHERE {} IS NULL)::bigint FROM {}"
        ).format(column, table_ref)
        cursor.execute(query)
        total, nulls = cursor.fetchone()
        pct = (float(nulls) / float(total) * 100) if total else 0.0
        failed = pct > config["max_null_pct"]
        return "fail" if failed else "pass", round(pct, 4), f"{nulls:,} of {total:,} rows are null ({pct:.2f}%)"

    if check_type == "duplicate_key":
        columns = sql.SQL(", ").join(sql.Identifier(column) for column in config["columns"])
        query = sql.SQL(
            "SELECT COALESCE(SUM(group_count - 1), 0)::bigint FROM "
            "(SELECT COUNT(*) AS group_count FROM {} GROUP BY {} HAVING COUNT(*) > 1) duplicates"
        ).format(table_ref, columns)
        cursor.execute(query)
        duplicate_rows = int(cursor.fetchone()[0] or 0)
        failed = duplicate_rows > config["max_duplicate_rows"]
        return "fail" if failed else "pass", float(duplicate_rows), f"Found {duplicate_rows:,} duplicate rows beyond the allowed first rows"

    column = sql.Identifier(config["column"])
    predicates = []
    params = []
    if config.get("min_value") is not None:
        predicates.append(sql.SQL("{} < %s").format(column))
        params.append(config["min_value"])
    if config.get("max_value") is not None:
        predicates.append(sql.SQL("{} > %s").format(column))
        params.append(config["max_value"])
    predicate = sql.SQL(" OR ").join(predicates)
    query = sql.SQL(
        "SELECT COUNT(*) FILTER (WHERE {} IS NOT NULL AND ({}))::bigint, MIN({}), MAX({}) FROM {}"
    ).format(column, predicate, column, column, table_ref)
    cursor.execute(query, params)
    invalid, observed_min, observed_max = cursor.fetchone()
    invalid = int(invalid or 0)
    return (
        "fail" if invalid else "pass",
        float(invalid),
        f"Found {invalid:,} out-of-range rows; observed minimum {observed_min}, maximum {observed_max}",
    )


def _resolve_check_incident(db, check_id: int, now: str, reason: str) -> int:
    result = db.execute(
        """UPDATE actions SET status = 'resolved', resolved_at = ?, updated_at = ?,
                  notes = COALESCE(notes, '') || ?
           WHERE check_id = ? AND type = 'data_quality'
             AND status NOT IN ('resolved', 'expected')""",
        (now, now, f" [auto-resolved: {reason}]", check_id),
    )
    db.execute(
        """UPDATE alerts SET resolution_status = 'resolved', resolved_at = ?,
                  acknowledged = 1, acknowledged_by = 'auto', resolution_reason = ?
           WHERE resolution_status IS NULL AND check_result_id IN (
               SELECT id FROM check_results WHERE check_id = ?
           )""",
        (now, reason, check_id),
    )
    return result.rowcount or 0


def _open_check_incident(db, check, source, result_id: int, message: str, now: str) -> int:
    fingerprint = f"data_quality:{check['id']}"
    owner = source["owner"] or _source_alert_owner(db, source["id"])
    notes = f"{check['name'] or CHECK_TYPES[check['type']]['label']}: {message}"
    existing = db.execute(
        """SELECT id, status FROM actions WHERE fingerprint = ? ORDER BY id DESC LIMIT 1""",
        (fingerprint,),
    ).fetchone()
    created = 0
    if existing and existing["status"] != "resolved":
        db.execute(
            """UPDATE actions SET assigned_to = ?, notes = ?, updated_at = ? WHERE id = ?""",
            (owner, notes, now, existing["id"]),
        )
    else:
        db.execute(
            """INSERT INTO actions
               (source_id, check_id, type, status, assigned_to, notes, fingerprint, created_at, updated_at)
               VALUES (?, ?, 'data_quality', 'open', ?, ?, ?, ?, ?)""",
            (source["id"], check["id"], owner, notes, fingerprint, now, now),
        )
        created = 1

    existing_alert = db.execute(
        """SELECT a.id FROM alerts a
           JOIN check_results cr ON cr.id = a.check_result_id
           WHERE cr.check_id = ? AND a.resolution_status IS NULL""",
        (check["id"],),
    ).fetchone()
    if not existing_alert:
        db.execute(
            """INSERT INTO alerts
               (check_result_id, source_id, severity, message, assigned_to, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (result_id, source["id"], check["severity"], notes, owner, now),
        )
    return created


def run_quality_checks(check_ids: list[int] | None = None) -> dict:
    """Execute enabled checks, store every result, and synchronize incidents."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        params = []
        where = "WHERE c.enabled = 1 AND COALESCE(s.archived, 0) = 0"
        if check_ids is not None:
            if not check_ids:
                return {"status": "completed", "total": 0, "pass": 0, "fail": 0, "error": 0, "skipped": 0}
            placeholders = ",".join("?" for _ in check_ids)
            where += f" AND c.id IN ({placeholders})"
            params.extend(check_ids)
        rows = db.execute(
            f"""SELECT c.*, s.name AS source_name, s.type AS source_type,
                       s.connection_info, s.owner AS source_owner, s.archived AS source_archived,
                       spi.server_name, spi.database_name, spi.schema_name,
                       spi.relation_name, spi.relation_kind
                FROM checks c JOIN sources s ON s.id = c.source_id
                LEFT JOIN source_postgres_identities spi ON spi.source_id = s.id
                {where} ORDER BY c.id""",
            params,
        ).fetchall()
        checks = [dict(row) for row in rows]

    pg_connections = {}
    counts = {"pass": 0, "fail": 0, "error": 0, "skipped": 0}
    created = 0
    resolved = 0
    results = []

    try:
        with get_db() as db:
            for check in checks:
                source = {
                    "id": check["source_id"],
                    "name": check["source_name"],
                    "type": check["source_type"],
                    "connection_info": check["connection_info"],
                    "owner": check["source_owner"],
                    "server_name": check["server_name"],
                    "database_name": check["database_name"],
                    "schema_name": check["schema_name"],
                    "relation_name": check["relation_name"],
                    "relation_kind": check["relation_kind"],
                }
                try:
                    config = validate_config(check["type"], json.loads(check["config"] or "{}"))
                    if CHECK_TYPES[check["type"]]["uses_postgres"]:
                        if source["type"] != "postgresql":
                            raise CheckDefinitionError("This check type requires a PostgreSQL source")
                        pg_conn, connection_error = _postgres_check_connection(source, pg_connections)
                        if connection_error:
                            status, value, message = "error", None, connection_error
                        else:
                            status, value, message = _run_postgres_check(
                                pg_conn,
                                source["schema_name"],
                                source["relation_name"],
                                check["type"],
                                config,
                            )
                    else:
                        status, value, message = _run_probe_check(db, source["id"], check["type"], config)
                except Exception as exc:
                    logger.exception("Data-quality check %s failed to execute", check["id"])
                    status, value, message = "error", None, str(exc)[:500]

                cursor = db.execute(
                    """INSERT INTO check_results (check_id, ran_at, status, value, message)
                       VALUES (?, ?, ?, ?, ?)""",
                    (check["id"], now, status, value, message),
                )
                db.execute("UPDATE checks SET updated_at = ? WHERE id = ?", (now, check["id"]))
                counts[status] = counts.get(status, 0) + 1
                if status in {"fail", "error"}:
                    created += _open_check_incident(db, check, source, cursor.lastrowid, message, now)
                elif status == "pass":
                    resolved += _resolve_check_incident(db, check["id"], now, "data-quality check passed")
                results.append({"check_id": check["id"], "status": status, "value": value, "message": message})
    finally:
        closed = set()
        for pg_conn in pg_connections.values():
            if pg_conn is None or id(pg_conn) in closed:
                continue
            closed.add(id(pg_conn))
            pg_conn.close()

    return {
        "status": "completed",
        "total": len(checks),
        **counts,
        "actions_created": created,
        "actions_resolved": resolved,
        "results": results,
    }


def resolve_disabled_check(check_id: int, reason: str = "check disabled") -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        return _resolve_check_incident(db, check_id, now, reason)
