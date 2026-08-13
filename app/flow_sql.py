"""PostgreSQL catalog discovery and optional Flows artifact handoff.

Credentials come from the existing dedicated DG_UPLOAD_* configuration. They
are never returned by the API, written to SQLite, or embedded in a flow job.
"""

from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path

from app.config import (
    UPLOAD_PGDATABASE,
    UPLOAD_PGHOST,
    UPLOAD_PGPASSWORD,
    UPLOAD_PGPORT,
    UPLOAD_PGUSER,
)

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


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
        raise RuntimeError("pandas/sqlalchemy are not installed. Re-run setup.ps1.") from exc
    url = URL.create(
        "postgresql+psycopg2",
        username=UPLOAD_PGUSER,
        password=UPLOAD_PGPASSWORD,
        host=UPLOAD_PGHOST,
        port=int(UPLOAD_PGPORT),
        database=database,
        query={"connect_timeout": "10"},
    )
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True)


def _quote_identifier(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value or ""):
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


def _read_artifact(path: Path):
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is not installed. Re-run setup.ps1.") from exc
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        frame = None
        # Flow artifacts have already passed through _normalize_csv, which
        # writes a standard comma-delimited file. Avoid pandas' Python parser
        # and delimiter inference here: on wide ASAP exports that redundant
        # pass can take many minutes before PostgreSQL COPY even starts.
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                frame = pd.read_csv(path, sep=",", engine="c", encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if frame is None:
            raise RuntimeError(f"Could not decode CSV artifact: {path.name}")
    else:
        raise RuntimeError("SQL handoff currently supports CSV files only.")
    if frame.empty:
        raise RuntimeError(f"Downloaded artifact has no data rows: {path.name}")
    seen: dict[str, int] = {}
    columns = []
    for index, column in enumerate(frame.columns):
        clean = re.sub(r"\W+", "_", str(column).strip()).strip("_").casefold() or f"col_{index}"
        seen[clean] = seen.get(clean, 0) + 1
        columns.append(clean)
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        raise RuntimeError(
            f"Downloaded CSV has duplicate column name(s) after normalization: {', '.join(duplicates)}"
        )
    frame.columns = columns
    return frame


def _copy_frame(connection, frame, qualified: str) -> None:
    """Stream a validated frame through PostgreSQL COPY in the open transaction."""
    columns = ", ".join(_quote_identifier(str(column)) for column in frame.columns)
    statement = (
        f"COPY {qualified} ({columns}) FROM STDIN "
        "WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
    )
    # The report is tens of megabytes. Spool locally instead of generating
    # thousands of individual INSERT statements or holding another full CSV
    # copy in memory. SQLAlchemy's transaction still owns commit/rollback.
    with tempfile.SpooledTemporaryFile(
        max_size=8 * 1024 * 1024, mode="w+", encoding="utf-8", newline="",
    ) as stream:
        frame.to_csv(stream, index=False, lineterminator="\n")
        stream.seek(0)
        raw_connection = connection.connection
        cursor = raw_connection.cursor()
        try:
            cursor.copy_expert(statement, stream)
        finally:
            cursor.close()


def load_artifacts(artifacts: list[dict], target: dict) -> dict:
    """Append artifacts, optionally truncating once, in one transaction."""
    from sqlalchemy import text

    database = str(target.get("database") or "")
    schema = str(target.get("schema") or "")
    table = str(target.get("table") or "")
    mode = str(target.get("mode") or "append").casefold()
    if mode not in {"append", "replace"}:
        raise ValueError("SQL write mode must be append or replace.")
    qualified = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    frames = [_read_artifact(Path(item["file_path"])) for item in artifacts]
    engine = _engine(database)
    rows_written = 0
    try:
        with engine.begin() as connection:
            column_rows = connection.execute(text(
                "SELECT column_name, is_nullable, column_default, is_identity, is_generated "
                "FROM information_schema.columns "
                "WHERE table_schema=:schema AND table_name=:table ORDER BY ordinal_position"
            ), {"schema": schema, "table": table}).fetchall()
            existing_columns = [row[0] for row in column_rows]
            if not existing_columns:
                raise RuntimeError(f"SQL target no longer exists: {database}.{schema}.{table}")
            for frame in frames:
                received = set(frame.columns)
                expected = set(existing_columns)
                required = {
                    row[0] for row in column_rows
                    if row[1] == "NO" and row[2] is None and row[3] == "NO" and row[4] == "NEVER"
                }
                extra = sorted(received - expected)
                missing = sorted(required - received)
                if extra or missing:
                    differences = []
                    if missing:
                        differences.append(f"missing: {', '.join(missing)}")
                    if extra:
                        differences.append(f"unexpected: {', '.join(extra)}")
                    raise RuntimeError(
                        f"CSV columns do not match {database}.{schema}.{table} "
                        f"({len(frame.columns)} CSV column(s), {len(existing_columns)} target column(s); "
                        f"{'; '.join(differences)}). No SQL changes were committed."
                    )
            if mode == "replace":
                connection.execute(text(f"TRUNCATE TABLE {qualified}"))
            for frame in frames:
                _copy_frame(connection, frame, qualified)
                rows_written += len(frame)
    finally:
        engine.dispose()
    return {"rows_written": rows_written, "files_loaded": len(frames), "mode": mode}
