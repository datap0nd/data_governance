"""Shared domain helpers for Pipeline relation samples and edge annotations."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

from app.database import get_db
from app.pipeline_insights_db import get_insights_db
from app.pipeline_insights_settings import get_pipeline_insights_settings
from app.source_identity import normalize_server, postgres_server_identity


SAMPLE_ROW_LIMIT = 15
AI_ROW_LIMIT = 100
MAX_CELL_CHARS = 1024
MAX_AI_CELL_CHARS = 512
MAX_SAMPLE_BYTES = 128 * 1024
PROMPT_VERSION = "pipeline-edge-v2-analyst"
RELATION_KINDS = frozenset({"table", "view", "materialized_view", "foreign_table"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_identity(identity: dict) -> str:
    return json.dumps(
        [
            normalize_server(identity.get("server_name")),
            str(identity.get("database_name") or "").strip(),
            str(identity.get("schema_name") or ""),
            str(identity.get("relation_name") or ""),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def relation_ref(identity: dict) -> str:
    return "pg:" + canonical_identity(identity)


def display_relation(identity: dict) -> str:
    return f"{identity['schema_name']}.{identity['relation_name']}"


def exclusion_labels(identity: dict) -> set[str]:
    server = normalize_server(identity.get("server_name"))
    database = str(identity.get("database_name") or "").strip()
    schema = str(identity.get("schema_name") or "")
    relation = str(identity.get("relation_name") or "")
    return {f"{server}/{database}/{schema}.{relation}"}


def pipeline_relations() -> list[dict]:
    """Return exact PostgreSQL relations rooted only in live report/Flow pipelines."""
    from app.config import UPLOAD_PGHOST, UPLOAD_PGPORT

    flow_server = postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT)
    with get_db() as db:
        rows = db.execute(
            """WITH RECURSIVE roots(id) AS (
                   SELECT DISTINCT rt.source_id
                     FROM report_tables rt
                     JOIN reports r ON r.id=rt.report_id
                     JOIN sources s ON s.id=rt.source_id
                    WHERE rt.source_id IS NOT NULL
                      AND COALESCE(r.archived, 0)=0
                      AND COALESCE(s.archived, 0)=0
                   UNION
                   SELECT DISTINCT f.sql_target_source_id
                     FROM flows f
                     JOIN sources s ON s.id=f.sql_target_source_id
                     JOIN source_postgres_identities flow_spi
                       ON flow_spi.source_id=f.sql_target_source_id
                    WHERE f.sql_handoff_enabled=1
                      AND f.sql_target_source_id IS NOT NULL
                      AND COALESCE(s.archived, 0)=0
                      AND flow_spi.server_name=?
                      AND flow_spi.database_name=f.sql_database
                      AND flow_spi.schema_name=f.sql_schema
                      AND flow_spi.relation_name=f.sql_table
               ), reachable(id) AS (
                   SELECT id FROM roots
                   UNION
                   SELECT sd.depends_on_id
                     FROM source_dependencies sd
                     JOIN reachable r ON r.id=sd.source_id
               )
               SELECT s.id AS source_id, spi.server_name, spi.database_name,
                      spi.schema_name, spi.relation_name, spi.relation_kind
                 FROM reachable r
                 JOIN sources s ON s.id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=s.id
                WHERE COALESCE(s.archived, 0)=0
                ORDER BY spi.server_name, spi.database_name, spi.schema_name,
                         spi.relation_name, s.id""",
            (flow_server,),
        ).fetchall()
    exclusions = set(get_pipeline_insights_settings().exclusions)
    result = []
    seen = set()
    for row in rows:
        item = dict(row)
        if item["relation_kind"] not in RELATION_KINDS:
            continue
        key = canonical_identity(item)
        if key in seen or exclusions.intersection(exclusion_labels(item)):
            continue
        seen.add(key)
        item["identity_key"] = key
        result.append(item)
    return result


def _safe_error_code(exc: Exception) -> str:
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
    if "permission" in message or "privilege" in message:
        return "permission_denied"
    if "timeout" in message or "canceling statement" in message:
        return "timeout"
    if "does not exist" in message or "undefined" in name:
        return "relation_missing"
    return "query_failed"


def _safe_error_message(code: str) -> str:
    return {
        "permission_denied": "The read-only scanner account cannot read this relation.",
        "timeout": "The read-only sample query timed out.",
        "relation_missing": "The relation no longer exists at its recorded identity.",
        "endpoint_unconfigured": "No configured read-only connection matches this endpoint.",
    }.get(code, "The relation could not be sampled; review the server log.")


def _json_value(value: Any, *, max_chars: int) -> tuple[Any, bool]:
    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else (str(value), True)
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, (datetime, date, time)):
        text = value.isoformat()
    elif isinstance(value, UUID):
        text = str(value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary: {len(value)} bytes>", True
    elif isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    else:
        text = str(value)
    if len(text) <= max_chars:
        return text, False
    suffix = "… [truncated]"
    return text[: max(0, max_chars - len(suffix))] + suffix, True


def _serialize_rows(
    rows: Iterable,
    *,
    max_cell_chars: int,
    max_payload_bytes: int,
) -> tuple[list[list[Any]], bool]:
    result: list[list[Any]] = []
    truncated = False
    for raw_row in rows:
        converted = []
        for value in raw_row:
            safe, cut = _json_value(value, max_chars=max_cell_chars)
            converted.append(safe)
            truncated = truncated or cut
        candidate = result + [converted]
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > max_payload_bytes:
            truncated = True
            break
        result.append(converted)
    return result, truncated


def _bounded_relation_payload(
    columns: list[dict], rows: list, *, max_cell_chars: int
) -> tuple[list[dict], list[list[Any]], bool]:
    """Cap the complete columns-and-rows JSON payload, not only row values."""
    bounded_columns = list(columns)
    truncated = False
    while bounded_columns and len(
        json.dumps({"columns": bounded_columns, "rows": []}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ) > MAX_SAMPLE_BYTES:
        bounded_columns.pop()
        truncated = True
    projected_rows = [tuple(row)[:len(bounded_columns)] for row in rows]
    encoded_columns = len(
        json.dumps({"columns": bounded_columns, "rows": []}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    bounded_rows, row_truncated = _serialize_rows(
        projected_rows,
        max_cell_chars=max_cell_chars,
        max_payload_bytes=max(2, MAX_SAMPLE_BYTES - encoded_columns),
    )
    truncated = truncated or row_truncated or len(bounded_columns) != len(columns)
    while bounded_rows and len(
        json.dumps(
            {"columns": bounded_columns, "rows": bounded_rows},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
    ) > MAX_SAMPLE_BYTES:
        bounded_rows.pop()
        truncated = True
    return bounded_columns, bounded_rows, truncated


def _quoted_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _connection_for_relation(identity: dict):
    from app.config import PGHOST, PGPORT, UPLOAD_PGHOST, UPLOAD_PGPORT
    from app.scanner.prober import _get_flow_pg_connection, _get_pg_connection

    server = normalize_server(identity.get("server_name"))
    primary = postgres_server_identity(PGHOST, PGPORT)
    flow = postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT)
    database = identity.get("database_name")
    if primary and server == primary:
        return _get_pg_connection(database=database)
    if flow and server == flow:
        return _get_flow_pg_connection(database=database)
    return None


def open_relation_connection(identity: dict):
    """Open the routed read-only connection used by bounded relation reads."""
    return _connection_for_relation(identity)


def extract_relation(
    identity: dict, *, limit: int, connection=None, close_connection: bool = True
) -> dict:
    """Execute bounded SELECT-only schema and row reads for one exact relation."""
    connection = connection or _connection_for_relation(identity)
    if connection is None:
        code = "endpoint_unconfigured"
        return {"status": "failed", "error_code": code, "error_message": _safe_error_message(code)}
    try:
        cursor = connection.cursor()
        cursor.execute(
            """SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)
                 FROM pg_catalog.pg_attribute a
                 JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname=%s
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""",
            (identity["schema_name"], identity["relation_name"]),
        )
        type_rows = cursor.fetchall()
        type_by_name = {str(row[0]): str(row[1]) for row in type_rows}
        qualified = (
            f"{_quoted_identifier(identity['schema_name'])}."
            f"{_quoted_identifier(identity['relation_name'])}"
        )
        cursor.execute(f"SELECT * FROM {qualified} LIMIT %s", (int(limit),))
        raw_rows = cursor.fetchall()
        columns = []
        for item in cursor.description or ():
            name = str(getattr(item, "name", item[0]))
            type_code = getattr(item, "type_code", item[1] if len(item) > 1 else None)
            columns.append({"name": name, "type": type_by_name.get(name, str(type_code or "unknown"))})
        columns, rows, truncated = _bounded_relation_payload(
            columns,
            raw_rows,
            max_cell_chars=(
                MAX_CELL_CHARS if limit <= SAMPLE_ROW_LIMIT else MAX_AI_CELL_CHARS
            ),
        )
        return {"status": "completed", "columns": columns, "rows": rows, "truncated": truncated}
    except Exception as exc:
        code = _safe_error_code(exc)
        return {"status": "failed", "error_code": code, "error_message": _safe_error_message(code)}
    finally:
        if close_connection:
            try:
                connection.close()
            except Exception:
                pass


def sample_hash(columns: list[dict], rows: list[list[Any]]) -> str:
    raw = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def save_relation_schema(identity_key: str, columns: list[dict], observed_at: str) -> None:
    with get_insights_db() as db:
        db.execute(
            """INSERT INTO relation_schemas(identity_key, columns_json, observed_at)
               VALUES (?, ?, ?)
               ON CONFLICT(identity_key) DO UPDATE SET
                   columns_json=excluded.columns_json, observed_at=excluded.observed_at""",
            (identity_key, json.dumps(columns, ensure_ascii=False, separators=(",", ":")), observed_at),
        )


def relation_schemas() -> dict[str, list[dict]]:
    with get_insights_db() as db:
        rows = db.execute("SELECT identity_key, columns_json FROM relation_schemas").fetchall()
    result = {}
    for row in rows:
        try:
            parsed = json.loads(row["columns_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
        result[row["identity_key"]] = parsed if isinstance(parsed, list) else []
    return result


def cached_sample_for_source(source_id: int) -> dict | None:
    with get_db() as db:
        identity_row = db.execute(
            """SELECT source_id, server_name, database_name, schema_name,
                      relation_name, relation_kind
                 FROM source_postgres_identities WHERE source_id=?""",
            (int(source_id),),
        ).fetchone()
    if identity_row is None:
        return None
    identity = dict(identity_row)
    exclusions = set(get_pipeline_insights_settings().exclusions)
    if exclusions.intersection(exclusion_labels(identity)):
        return {
            **identity,
            "identity_key": canonical_identity(identity),
            "columns": [],
            "rows": [],
            "sampled_at": None,
            "last_attempt_at": None,
            "last_attempt_status": "excluded",
            "error_code": "excluded",
            "error_message": "This exact relation is excluded from cached previews.",
            "truncated": False,
            "stale": False,
            "unordered": True,
        }
    key = canonical_identity(identity)
    with get_insights_db() as db:
        row = db.execute("SELECT * FROM relation_samples WHERE identity_key=?", (key,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    for name in ("columns_json", "rows_json"):
        try:
            data[name.removesuffix("_json")] = json.loads(data.pop(name) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            data[name.removesuffix("_json")] = []
    data["truncated"] = bool(data["truncated"])
    data["stale"] = bool(
        data.get("sampled_at") and data["last_attempt_status"] != "completed"
    )
    data["unordered"] = True
    return data


def edge_key(kind: str, from_key: str, to_key: str) -> str:
    raw = json.dumps([kind, from_key, to_key], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def edge_annotations(keys: Iterable[str]) -> dict[str, dict]:
    unique = list(dict.fromkeys(str(key) for key in keys if key))
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    with get_insights_db() as db:
        rows = db.execute(
            f"SELECT * FROM edge_explanations WHERE edge_key IN ({placeholders})",
            unique,
        ).fetchall()
    return {row["edge_key"]: dict(row) for row in rows}


def group_relations_by_endpoint(relations: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for relation in relations:
        grouped[(normalize_server(relation.get("server_name")), relation["database_name"])].append(relation)
    return grouped
