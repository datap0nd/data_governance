"""PostgreSQL catalog discovery and optional Flows artifact handoff.

Credentials come from the existing dedicated DG_UPLOAD_* configuration. They
are never returned by the API, written to SQLite, or embedded in a flow job.
"""

from __future__ import annotations

import csv
import re
import time
import uuid
from pathlib import Path
from typing import Callable

from app.config import (
    UPLOAD_PGDATABASE,
    UPLOAD_PGHOST,
    UPLOAD_PGPASSWORD,
    UPLOAD_PGPORT,
    UPLOAD_PGUSER,
)

# These are runaway backstops, not performance budgets. A load is allowed to
# take as long as the database needs on a slow day; the limits exist only so a
# wedged connection, an unreachable host, or a lock nobody will ever release
# cannot leave a transaction open forever.
SQL_CONNECT_TIMEOUT_SECONDS = 60
# How long to wait for another session's lock on the target table. Unbounded
# would let one long-running reader pin an open Metronome transaction all day.
SQL_LOCK_TIMEOUT_SECONDS = 10 * 60
# Per-statement ceiling. A multi-million-row COPY, and the TRUNCATE plus
# INSERT ... SELECT a managed snapshot runs after it, each get the full budget.
SQL_STATEMENT_TIMEOUT_SECONDS = 30 * 60
SqlProgress = Callable[[dict], None]


class SqlHandoffError(RuntimeError):
    """A stage-specific, credential-safe SQL handoff failure."""

    def __init__(self, stage: str, target: str, cause: Exception, outcome: str):
        self.stage = stage
        self.target = target
        self.cause = cause
        self.outcome = outcome
        super().__init__(
            f"SQL {stage.replace('_', ' ')} failed for {target}: "
            f"{_database_error(cause)} {outcome}"
        )


def configuration_status() -> dict:
    missing = []
    if not UPLOAD_PGHOST:
        missing.append("PGHOST (or DG_UPLOAD_PGHOST)")
    if not UPLOAD_PGDATABASE:
        missing.append("PGDATABASE (or DG_UPLOAD_PGDATABASE)")
    if not UPLOAD_PGUSER:
        missing.append("DG_UPLOAD_PGUSER")
    if not UPLOAD_PGPASSWORD:
        missing.append("DG_UPLOAD_PGPASSWORD")
    return {
        "configured": not missing,
        "missing": missing,
        "host": UPLOAD_PGHOST,
        "default_database": UPLOAD_PGDATABASE,
    }


def _engine(database: str):
    status = configuration_status()
    if not status["configured"]:
        raise RuntimeError(f"SQL handoff is not configured. Missing: {', '.join(status['missing'])}")
    if not isinstance(database, str) or not database.strip() or len(database) > 200 or "\x00" in database:
        raise ValueError("Choose a valid database from the discovered SQL catalog.")
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        from sqlalchemy.pool import NullPool
    except ImportError as exc:
        raise RuntimeError("sqlalchemy/psycopg2 are not installed. Re-run setup.ps1.") from exc
    url = URL.create(
        "postgresql+psycopg2",
        username=UPLOAD_PGUSER,
        password=UPLOAD_PGPASSWORD,
        host=UPLOAD_PGHOST,
        port=int(UPLOAD_PGPORT),
        database=database,
        query={
            "connect_timeout": str(SQL_CONNECT_TIMEOUT_SECONDS),
            "keepalives": "1",
            "keepalives_idle": "15",
            "keepalives_interval": "5",
            "keepalives_count": "3",
            "application_name": "Metronome Flows",
        },
    )
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True)


def _quote_identifier(value: str) -> str:
    """Safely quote an exact PostgreSQL catalog identifier.

    Discovered schema and table names can legitimately contain spaces or other
    punctuation. PostgreSQL keeps those names safe inside double quotes, with
    any embedded double quote represented twice. NUL is the one character a
    PostgreSQL identifier cannot contain.
    """
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value.encode("utf-8")) > 63
    ):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def discover_catalog() -> dict:
    """Read every database/schema/table accessible to the configured role."""
    from sqlalchemy import text

    started = time.perf_counter()
    seed = _engine(UPLOAD_PGDATABASE)
    try:
        with seed.connect() as connection:
            databases = [
                row[0] for row in connection.execute(text(
                    "SELECT datname FROM pg_database "
                    "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
                ))
            ]
    finally:
        seed.dispose()
    targets = []
    errors = []
    for database in databases:
        engine = None
        try:
            engine = _engine(database)
            with engine.connect() as connection:
                rows = connection.execute(text(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_type='BASE TABLE' "
                    "AND table_schema NOT IN ('pg_catalog','information_schema') "
                    "AND table_schema NOT LIKE 'pg_toast%' "
                    "ORDER BY table_schema, table_name"
                )).fetchall()
            targets.extend({"database": database, "schema": row[0], "table": row[1]} for row in rows)
        except Exception as exc:
            errors.append({"database": database, "error": str(exc)[:1000]})
        finally:
            if engine is not None:
                engine.dispose()
    return {
        "targets": targets,
        "database_count": len(databases),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "errors": errors,
    }


def _normalized_column_name(column: object, index: int) -> str:
    return re.sub(r"\W+", "_", str(column).strip()).strip("_").casefold() or f"col_{index}"


def _normalized_columns(header: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    columns = []
    for index, column in enumerate(header):
        clean = _normalized_column_name(column, index)
        seen[clean] = seen.get(clean, 0) + 1
        columns.append(clean)
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        raise RuntimeError(
            f"Downloaded CSV has duplicate column name(s) after normalization: {', '.join(duplicates)}"
        )
    return columns


def _target_columns_by_normalized_name(columns: list[str]) -> dict[str, str]:
    """Map normalized CSV names back to exact PostgreSQL target names."""
    mapped: dict[str, str] = {}
    ambiguous: set[str] = set()
    for index, column in enumerate(columns):
        normalized = _normalized_column_name(column, index)
        if normalized in mapped and mapped[normalized] != column:
            ambiguous.add(normalized)
        mapped[normalized] = column
    if ambiguous:
        raise RuntimeError(
            "SQL target has ambiguous column name(s) after normalization: "
            + ", ".join(sorted(ambiguous))
        )
    return mapped


def _read_artifact(path: Path, known_row_count: int | None = None) -> dict:
    """Inspect a normalized CSV without loading its data into memory."""
    if path.suffix.casefold() != ".csv":
        raise RuntimeError("SQL handoff currently supports CSV files only.")
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Downloaded artifact is missing or empty: {path.name}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            first_row = next(reader, None)
            if not header:
                raise RuntimeError(f"Downloaded artifact has no header: {path.name}")
            if first_row is None:
                raise RuntimeError(f"Downloaded artifact has no data rows: {path.name}")
            row_count = (
                known_row_count
                if isinstance(known_row_count, int) and known_row_count > 0
                else 1 + sum(1 for _ in reader)
            )
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"Downloaded artifact is not normalized UTF-8 CSV: {path.name}"
        ) from exc
    except csv.Error as exc:
        raise RuntimeError(f"Downloaded artifact is invalid CSV: {path.name}: {exc}") from exc
    return {
        "path": path,
        "filename": path.name,
        "columns": _normalized_columns(header),
        "row_count": row_count,
        "file_size": path.stat().st_size,
    }


def _copy_artifact(connection, artifact: dict, qualified: str) -> None:
    """Stream one normalized CSV directly through PostgreSQL COPY."""
    target_columns = artifact.get("copy_columns") or artifact["columns"]
    columns = ", ".join(_quote_identifier(str(column)) for column in target_columns)
    statement = (
        f"COPY {qualified} ({columns}) FROM STDIN "
        "WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
    )
    raw_connection = connection.connection
    cursor = raw_connection.cursor()
    try:
        with artifact["path"].open("r", encoding="utf-8-sig", newline="") as stream:
            cursor.copy_expert(statement, stream)
    finally:
        cursor.close()


def _database_error(exc: Exception) -> str:
    """Return the useful PostgreSQL message without credentials or a DSN."""
    original = getattr(exc, "orig", None) or exc
    sqlstate = getattr(original, "pgcode", None)
    diagnostic = getattr(original, "diag", None)
    primary = getattr(diagnostic, "message_primary", None) if diagnostic is not None else None
    message = str(primary or original or exc).strip().replace("\r", " ").replace("\n", " ")
    if UPLOAD_PGPASSWORD:
        message = message.replace(UPLOAD_PGPASSWORD, "[redacted]")
    message = re.sub(r"\s+", " ", message)[:4000]
    prefix = f"PostgreSQL SQLSTATE {sqlstate}: " if sqlstate else ""
    return prefix + (message or type(original).__name__)


def _emit(progress: SqlProgress | None, stage: str, message: str, started: float, **details) -> None:
    if progress is None:
        return
    progress({
        "stage": stage,
        "message": message,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        **details,
    })


def load_artifacts(
    artifacts: list[dict], target: dict, progress: SqlProgress | None = None,
) -> dict:
    """Append to a target, or atomically replace all of its rows."""
    from sqlalchemy import text

    started = time.perf_counter()
    database = str(target.get("database") or "")
    schema = str(target.get("schema") or "")
    table = str(target.get("table") or "")
    mode = str(target.get("mode") or "append").casefold()
    if mode not in {"append", "replace"}:
        raise ValueError("SQL write mode must be append or replace.")
    qualified = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    target_name = f"{database}.{schema}.{table}"
    if not artifacts:
        raise RuntimeError("SQL handoff received no CSV artifacts.")

    stage = "artifact validation"
    _emit(
        progress, "sql_artifact_validation", f"Validating {len(artifacts)} CSV file(s) for SQL.",
        started, status="started", target=target_name,
    )
    inspected = [
        _read_artifact(Path(item["file_path"]), item.get("row_count"))
        for item in artifacts
    ]
    total_rows = sum(item["row_count"] for item in inspected)
    _emit(
        progress, "sql_artifact_validation", f"Validated {len(inspected)} CSV file(s) with {total_rows} row(s).",
        started, status="completed", target=target_name, files=len(inspected), rows=total_rows,
        bytes=sum(item["file_size"] for item in inspected),
    )

    engine = None
    connection = None
    transaction = None
    commit_started = False
    rollback_status = "The SQL transaction never started. No SQL changes were committed."
    rows_written = 0
    schema_action = "append" if mode == "append" else "managed_snapshot"
    target_created = False
    columns_added: list[str] = []
    copy_destination = qualified
    try:
        stage = "connection"
        _emit(
            progress, "sql_connecting", f"Connecting to PostgreSQL database {database}.",
            started, status="started", target=target_name,
            timeout_seconds=SQL_CONNECT_TIMEOUT_SECONDS,
        )
        engine = _engine(database)
        connection = engine.connect()
        _emit(
            progress, "sql_connected", f"Connected to PostgreSQL database {database}.",
            started, status="completed", target=target_name,
        )

        transaction = connection.begin()
        rollback_status = "The transaction did not commit. PostgreSQL rollback was requested."
        connection.execute(text(f"SET LOCAL lock_timeout = '{SQL_LOCK_TIMEOUT_SECONDS}s'"))
        connection.execute(text(f"SET LOCAL statement_timeout = '{SQL_STATEMENT_TIMEOUT_SECONDS}s'"))

        stage = "target validation"
        _emit(
            progress, "sql_target_validation", f"Reading target columns for {target_name}.",
            started, status="started", target=target_name,
        )
        column_rows = connection.execute(text(
            "SELECT column_name, is_nullable, column_default, is_identity, is_generated "
            "FROM information_schema.columns "
            "WHERE table_schema=:schema AND table_name=:table ORDER BY ordinal_position"
        ), {"schema": schema, "table": table}).fetchall()
        existing_columns = [row[0] for row in column_rows]
        if mode == "append":
            if not existing_columns:
                raise RuntimeError(f"SQL target no longer exists: {target_name}")
            target_columns = _target_columns_by_normalized_name(existing_columns)
            for artifact in inspected:
                mapped_columns = [
                    target_columns[column]
                    for column in artifact["columns"]
                    if column in target_columns
                ]
                required = {
                    row[0] for row in column_rows
                    if row[1] == "NO" and row[2] is None and row[3] == "NO" and row[4] == "NEVER"
                }
                extra = sorted(column for column in artifact["columns"] if column not in target_columns)
                missing = sorted(required - set(mapped_columns))
                if extra or missing:
                    differences = []
                    if missing:
                        differences.append(f"missing: {', '.join(missing)}")
                    if extra:
                        differences.append(f"unexpected: {', '.join(extra)}")
                    raise RuntimeError(
                        f"CSV columns do not match {target_name} "
                        f"({len(artifact['columns'])} CSV column(s), {len(existing_columns)} target column(s); "
                        f"{'; '.join(differences)})"
                    )
                artifact["copy_columns"] = mapped_columns
            _emit(
                progress, "sql_target_validation", f"CSV columns match {target_name} for append.",
                started, status="completed", target=target_name,
                csv_columns=len(inspected[0]["columns"]), target_columns=len(existing_columns),
                mode=mode,
            )
        else:
            replacement_columns = []
            replacement_seen: set[str] = set()
            for artifact in inspected:
                for column in artifact["columns"]:
                    if column not in replacement_seen:
                        replacement_seen.add(column)
                        replacement_columns.append(column)
            target_columns = (
                _target_columns_by_normalized_name(existing_columns)
                if existing_columns else {}
            )
            destination_columns = [
                target_columns.get(column, column) for column in replacement_columns
            ]
            columns_added = [
                column for column in replacement_columns if column not in target_columns
            ]
            required = {
                row[0] for row in column_rows
                if row[1] == "NO" and row[2] is None and row[3] == "NO" and row[4] == "NEVER"
            }
            missing = sorted(required - set(destination_columns))
            if missing:
                raise RuntimeError(
                    f"Replacing all rows cannot preserve required target column(s) absent from CSV: "
                    f"{', '.join(missing)}"
                )
            schema_action = "refresh" if existing_columns else "create"
            _emit(
                progress, "sql_target_validation",
                (
                    f"Target {target_name} exists; its rows will be replaced and the table preserved, "
                    f"add {len(columns_added)} column(s), and replace all rows."
                    if existing_columns else
                    f"Target {target_name} does not exist; it will be created "
                    f"with {len(replacement_columns)} TEXT column(s)."
                ),
                started, status="completed", target=target_name,
                csv_columns=len(replacement_columns), target_columns=len(existing_columns),
                mode=mode, schema_action=schema_action, columns_added=len(columns_added),
                column_type="TEXT", files=len(inspected), schema_union=True,
            )

        if mode == "replace":
            stage = "staging"
            staging_table = f"_metronome_stage_{uuid.uuid4().hex[:16]}"
            staging_qualified = f"{_quote_identifier(schema)}.{_quote_identifier(staging_table)}"
            _emit(
                progress, "sql_staging",
                f"Creating a staging table for {target_name} with "
                f"{len(replacement_columns)} TEXT column(s).",
                started, status="started", target=target_name,
                schema_action=schema_action,
            )
            column_definitions = ", ".join(
                f"{_quote_identifier(column)} TEXT" for column in replacement_columns
            )
            connection.execute(text(f"CREATE TABLE {staging_qualified} ({column_definitions})"))
            copy_destination = staging_qualified
            _emit(
                progress, "sql_staging",
                f"Created the uncommitted staging table for {target_name}.",
                started, status="completed", target=target_name,
                columns=len(replacement_columns), schema_action=schema_action,
            )

        for index, artifact in enumerate(inspected, start=1):
            stage = "copy"
            _emit(
                progress, "sql_copy", f"COPY {index} of {len(inspected)} started: {artifact['filename']}.",
                started, status="started", target=target_name, file=artifact["filename"],
                file_index=index, files=len(inspected), rows=artifact["row_count"],
                bytes=artifact["file_size"], timeout_seconds=SQL_STATEMENT_TIMEOUT_SECONDS,
            )
            _copy_artifact(connection, artifact, copy_destination)
            rows_written += artifact["row_count"]
            _emit(
                progress, "sql_copy", f"COPY {index} of {len(inspected)} completed: {artifact['filename']}.",
                started, status="completed", target=target_name, file=artifact["filename"],
                file_index=index, files=len(inspected), rows=artifact["row_count"],
            )

        if mode == "replace":
            stage = "replace"
            _emit(
                progress, "sql_replace",
                (
                    f"Creating {target_name} from the staged rows."
                    if not existing_columns else
                    f"Replacing all rows in {target_name} while preserving the table itself."
                ),
                started, status="started", target=target_name,
                timeout_seconds=SQL_LOCK_TIMEOUT_SECONDS, schema_action=schema_action,
                rows=rows_written, columns_added=len(columns_added),
            )
            if not existing_columns:
                connection.execute(text(
                    f"ALTER TABLE {staging_qualified} RENAME TO {_quote_identifier(table)}"
                ))
                target_created = True
            else:
                for column in columns_added:
                    connection.execute(text(
                        f"ALTER TABLE {qualified} ADD COLUMN {_quote_identifier(column)} TEXT"
                    ))
                destination_sql = ", ".join(
                    _quote_identifier(column) for column in destination_columns
                )
                staging_sql = ", ".join(
                    _quote_identifier(column) for column in replacement_columns
                )
                connection.execute(text(f"TRUNCATE TABLE {qualified}"))
                connection.execute(text(
                    f"INSERT INTO {qualified} ({destination_sql}) "
                    f"SELECT {staging_sql} FROM {staging_qualified}"
                ))
                connection.execute(text(f"DROP TABLE {staging_qualified}"))
            _emit(
                progress, "sql_replace",
                (
                    f"Created {target_name} from the staged rows."
                    if target_created else
                    f"Replaced all rows in {target_name}; the table, grants, indexes, and constraints remain."
                ),
                started, status="completed", target=target_name,
                schema_action=schema_action, rows=rows_written,
                columns_added=len(columns_added),
            )

        stage = "commit"
        commit_started = True
        _emit(
            progress, "sql_commit", f"Committing {rows_written} row(s) to {target_name}.",
            started, status="started", target=target_name, rows=rows_written,
        )
        transaction.commit()
        transaction = None
        rollback_status = "PostgreSQL confirmed commit."
        _emit(
            progress, "sql_commit", f"Committed {rows_written} row(s) to {target_name}.",
            started, status="completed", target=target_name, rows=rows_written,
        )
    except Exception as exc:
        if transaction is not None:
            try:
                transaction.rollback()
                rollback_status = "PostgreSQL confirmed rollback. No SQL changes were committed."
            except Exception:
                rollback_status = (
                    "Commit outcome is unknown because the connection failed during commit. "
                    "Check the target before retrying append."
                    if commit_started else
                    "PostgreSQL rollback could not be confirmed. Check the target before retrying."
                )
        _emit(
            progress, "sql_failed", f"SQL {stage} failed for {target_name}: {_database_error(exc)}",
            started, status="failed", target=target_name, sql_stage=stage,
            outcome=rollback_status,
        )
        raise SqlHandoffError(stage, target_name, exc, rollback_status) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass

    return {
        "rows_written": rows_written,
        "files_loaded": len(inspected),
        "mode": mode,
        "target": target_name,
        "schema_replaced": False,
        "snapshot_refreshed": mode == "replace",
        "target_created": target_created,
        "columns_added": columns_added,
        "column_type": "TEXT" if mode == "replace" else None,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
