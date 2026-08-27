"""Import Data - load a CSV/Excel file into a PostgreSQL table.

POST /preview parses the uploaded file and stages it under a token. POST /load
writes the staged file into the target table for one-time table creation or
manual imports. POST /script creates a recurring append/replace import script.
Replace runs TRUNCATE + INSERT in one transaction so a failed load leaves the
previous data intact.

Writes use the dedicated DG_UPLOAD_* credentials from config, never the
read-only probing credentials (PGUSER/PGPASSWORD).
"""

import json
import logging
import re
import shutil
import tempfile
import textwrap
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.config import (
    BASE_DIR,
    IMPORT_SCRIPT_DIR,
    UPLOAD_PGDATABASE,
    UPLOAD_PGHOST,
    UPLOAD_PGPASSWORD,
    UPLOAD_PGPORT,
    UPLOAD_PGUSER,
    UPLOAD_SCHEMA,
)
from app.database import get_db
from app.routers.eventlog import get_actor, log_event
from app.settings import get_setting, set_setting
from app.source_identity import normalize_server

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data-import", tags=["data-import"])

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# The target schema comes from the DG_UPLOAD_SCHEMA env var and is interpolated
# directly into the TRUNCATE statement, so it must be a bare SQL identifier.
# It is operator-set (not user input) and not lowercased, so allow mixed case
# and the dollar sign Postgres permits; reject anything that could break out of
# the quoted identifier (quotes, whitespace, dots, semicolons, ...).
SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SCRIPT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CRON_FIELD_RE = re.compile(r"^[A-Za-z0-9_*/,\-?#LW]+$")
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_./+\-]+$")
STAGING_DIR = Path(tempfile.gettempdir()) / "dg_data_import"
STAGING_MAX_AGE_SECONDS = 2 * 3600
ALLOWED_EXTENSIONS = (".csv", ".xlsx", ".xls")
SCRIPT_DIR_SETTING = "import_script_dir"


class MaterializedViewRef(BaseModel):
    schema: str
    name: str


class LoadRequest(BaseModel):
    token: str
    table: str
    mode: str  # append | replace | create
    materialized_views: list[MaterializedViewRef] = Field(default_factory=list)


class RefreshMaterializedViewsRequest(BaseModel):
    materialized_views: list[MaterializedViewRef] = Field(default_factory=list)


class PrefectScheduleRequest(BaseModel):
    type: str = "manual"  # manual | daily | weekly | cron
    cron: str | None = None
    timezone: str | None = "UTC"


class GenerateScriptRequest(BaseModel):
    token: str
    table: str
    mode: str  # append | replace
    materialized_views: list[MaterializedViewRef] = Field(default_factory=list)
    schedule: PrefectScheduleRequest = Field(default_factory=PrefectScheduleRequest)
    script_name: str | None = None


class ScriptDirRequest(BaseModel):
    path: str


def _missing_config() -> list[str]:
    missing = []
    if not UPLOAD_PGHOST:
        missing.append("PGHOST (or DG_UPLOAD_PGHOST)")
    if not UPLOAD_PGDATABASE:
        missing.append("PGDATABASE (or DG_UPLOAD_PGDATABASE)")
    if not UPLOAD_PGUSER:
        missing.append("DG_UPLOAD_PGUSER")
    if not UPLOAD_PGPASSWORD:
        missing.append("DG_UPLOAD_PGPASSWORD")
    return missing


def _get_engine():
    """Build a SQLAlchemy engine for the write connection.

    Lazy imports so the panel keeps running when pandas/sqlalchemy are not
    installed; the section then reports itself as unavailable instead.
    """
    missing = _missing_config()
    if missing:
        raise HTTPException(400, f"Import is not configured. Missing: {', '.join(missing)}")
    if not SCHEMA_RE.match(UPLOAD_SCHEMA):
        raise HTTPException(
            500,
            f"Configured target schema is not a valid identifier: {UPLOAD_SCHEMA!r}. "
            "Set DG_UPLOAD_SCHEMA to a plain schema name.",
        )
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        from sqlalchemy.pool import NullPool
    except ImportError:
        raise HTTPException(500, "pandas/sqlalchemy are not installed on this machine. Re-run setup.ps1 to install dependencies.")
    url = URL.create(
        "postgresql+psycopg2",
        username=UPLOAD_PGUSER,
        password=UPLOAD_PGPASSWORD,
        host=UPLOAD_PGHOST,
        port=int(UPLOAD_PGPORT),
        database=UPLOAD_PGDATABASE,
        query={"connect_timeout": "10"},
    )
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True)


def _quote_ident(value: str) -> str:
    """Quote a PostgreSQL identifier after validating our supported name shape."""
    value = value.strip()
    if not SCHEMA_RE.match(value):
        raise HTTPException(400, f"Invalid SQL identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def _qualified_name(schema: str, name: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(name)}"


def _postgres_error(action: str, exc: Exception) -> str:
    message = str(exc)
    lower = message.lower()
    if "remaining connection slots are reserved" in lower or "too many clients already" in lower:
        return (
            f"{action}: PostgreSQL has no free connection slots for this role. "
            "Close idle database sessions or raise the database connection limit, then retry."
        )
    return f"{action}: {message}"


def _script_dir_setting() -> str:
    value = (get_setting(SCRIPT_DIR_SETTING, IMPORT_SCRIPT_DIR) or IMPORT_SCRIPT_DIR).strip()
    return value or IMPORT_SCRIPT_DIR


def _resolve_script_dir(raw_path: str) -> Path:
    raw_path = (raw_path or "").strip()
    if not raw_path:
        raise HTTPException(400, "Script folder cannot be blank.")
    if "\x00" in raw_path:
        raise HTTPException(400, "Script folder contains an invalid character.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _current_script_dir() -> Path:
    return _resolve_script_dir(_script_dir_setting())


def _clean_table_name(table: str) -> str:
    """Normalize a user-supplied table name before it reaches SQL.

    Table names are always lowercased and any whitespace is converted to
    underscores, so "My Table" becomes "my_table". Anything else that is not
    a plain lowercase identifier is rejected.
    """
    table = re.sub(r"\s+", "_", (table or "").strip()).lower()
    if not NAME_RE.match(table):
        raise HTTPException(400, f"Invalid table name: {table} (letters, numbers, underscores; start with a letter)")
    return table


def _clean_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in ("append", "replace", "create"):
        raise HTTPException(400, f"Invalid mode: {mode}")
    return mode


def _clean_timezone(timezone: str | None) -> str:
    timezone = (timezone or "UTC").strip()
    if not timezone:
        timezone = "UTC"
    if len(timezone) > 80 or not TIMEZONE_RE.match(timezone):
        raise HTTPException(400, "Invalid Prefect schedule timezone. Use an IANA timezone like UTC or America/New_York.")
    return timezone


def _clean_cron(cron: str | None) -> str:
    cron = re.sub(r"\s+", " ", (cron or "").strip())
    fields = cron.split(" ")
    if len(fields) != 5 or any(not CRON_FIELD_RE.match(field) for field in fields):
        raise HTTPException(400, "Prefect cron schedule must use five cron fields, for example: 0 8 * * 1")
    return cron


def _clean_prefect_schedule(schedule: PrefectScheduleRequest | None) -> dict:
    schedule = schedule or PrefectScheduleRequest()
    schedule_type = (schedule.type or "manual").strip().lower()
    if schedule_type not in ("manual", "daily", "weekly", "cron"):
        raise HTTPException(400, f"Invalid Prefect schedule type: {schedule_type}")

    timezone = _clean_timezone(schedule.timezone)
    if schedule_type == "manual":
        return {
            "type": "manual",
            "cron": None,
            "timezone": timezone,
            "label": "Manual / run once",
        }

    cron = _clean_cron(schedule.cron)
    label_prefix = {
        "daily": "Daily",
        "weekly": "Weekly",
        "cron": "Custom cron",
    }[schedule_type]
    return {
        "type": schedule_type,
        "cron": cron,
        "timezone": timezone,
        "label": f"{label_prefix}: {cron} ({timezone})",
    }


def _read_dataframe(path: Path, original_name: str):
    """Parse a staged CSV/Excel file and normalize its column names."""
    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(500, "pandas is not installed on this machine. Re-run setup.ps1 to install dependencies.")

    ext = Path(original_name).suffix.lower()
    if ext == ".csv":
        df = None
        for enc in ("utf-8", "cp1252", "latin-1"):  # cp1252 = Windows Excel exports
            try:
                df = pd.read_csv(path, sep=None, engine="python", encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if df is None:
            raise HTTPException(400, "Could not decode the CSV file.")
    elif ext in (".xlsx", ".xls"):
        try:
            df = pd.read_excel(path)
        except Exception as e:
            raise HTTPException(400, f"Could not read the Excel file: {e}")
    else:
        raise HTTPException(400, f"Unsupported file type: {ext} (use .csv, .xlsx or .xls)")

    if df.empty:
        raise HTTPException(400, "The file has no rows.")

    seen: dict[str, int] = {}
    cols = []
    for i, c in enumerate(df.columns):
        c = re.sub(r"\W+", "_", str(c).strip()).strip("_").lower() or f"col_{i}"
        seen[c] = seen.get(c, 0) + 1
        cols.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    df.columns = cols
    return df


def _purge_stale_staging():
    if not STAGING_DIR.is_dir():
        return
    cutoff = time.time() - STAGING_MAX_AGE_SECONDS
    for f in STAGING_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _staged_file(token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise HTTPException(400, "Invalid staging token.")
    matches = list(STAGING_DIR.glob(f"{token}__*")) if STAGING_DIR.is_dir() else []
    if not matches:
        raise HTTPException(400, "Staged file not found or expired. Upload the file again.")
    return matches[0]


def _existing_tables(engine) -> list[str]:
    from sqlalchemy import text

    q = text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = :s AND table_type = 'BASE TABLE' ORDER BY table_name"
    )
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(q, {"s": UPLOAD_SCHEMA})]


def _materialized_views(engine) -> list[dict]:
    """Return materialized views from the target PostgreSQL database."""
    from sqlalchemy import text

    q = text(
        "SELECT schemaname, matviewname "
        "FROM pg_matviews "
        "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY schemaname, matviewname"
    )
    with engine.connect() as conn:
        rows = conn.execute(q).fetchall()

    views = [
        {
            "schema": r[0],
            "name": r[1],
            "display_name": f"{r[0]}.{r[1]}",
            "source_id": None,
            "refresh_schedule": None,
        }
        for r in rows
    ]

    try:
        with get_db() as db:
            for view in views:
                full_name = view["display_name"]
                source = db.execute(
                    """SELECT id, refresh_schedule
                       FROM sources
                       WHERE archived = 0
                         AND (name LIKE ? OR connection_info LIKE ?)
                       LIMIT 1""",
                    (f"%{full_name}", f"%{full_name}%"),
                ).fetchone()
                if source:
                    view["source_id"] = source["id"]
                    view["refresh_schedule"] = source["refresh_schedule"]
    except Exception:
        logger.debug("Could not enrich materialized view list from local sources", exc_info=True)

    return views


def _validate_materialized_views(engine, requested: list[MaterializedViewRef]) -> list[dict]:
    if not requested:
        return []

    available = {
        (v["schema"], v["name"]): v
        for v in _materialized_views(engine)
    }
    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ref in requested:
        schema = ref.schema.strip()
        name = ref.name.strip()
        _quote_ident(schema)
        _quote_ident(name)
        key = (schema, name)
        if key not in available:
            raise HTTPException(400, f"Materialized view not found: {schema}.{name}")
        if key not in seen:
            selected.append({"schema": schema, "name": name, "display_name": f"{schema}.{name}"})
            seen.add(key)
    return selected


def _refresh_materialized_views(engine, views: list[dict]) -> list[str]:
    """Refresh materialized views one by one and return qualified names."""
    if not views:
        return []

    from sqlalchemy import text

    refreshed = []
    with engine.begin() as conn:
        for view in views:
            schema = view["schema"]
            name = view["name"]
            conn.execute(text(f"REFRESH MATERIALIZED VIEW {_qualified_name(schema, name)}"))
            refreshed.append(f"{schema}.{name}")
    return refreshed


def _validate_table_load(engine, df, table: str, mode: str) -> list[str]:
    from sqlalchemy import text

    existing = set(_existing_tables(engine))
    if mode == "create" and table in existing:
        raise HTTPException(400, f"{UPLOAD_SCHEMA}.{table} already exists. Use append or replace.")
    if mode != "create" and table not in existing:
        raise HTTPException(400, f"{UPLOAD_SCHEMA}.{table} does not exist. Use create.")

    null_columns: list[str] = []
    if mode != "create":
        q = text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"
        )
        with engine.connect() as conn:
            table_cols = {r[0] for r in conn.execute(q, {"s": UPLOAD_SCHEMA, "t": table})}
        extra = [c for c in df.columns if c not in table_cols]
        if extra:
            raise HTTPException(
                400,
                f"The file has columns that don't exist in {UPLOAD_SCHEMA}.{table}: {', '.join(extra)}. "
                f"Table columns are: {', '.join(sorted(table_cols))}",
            )
        null_columns = sorted(table_cols - set(df.columns))

    return null_columns


def _write_dataframe_to_table(engine, df, table: str, mode: str) -> list[str]:
    from sqlalchemy import text

    null_columns = _validate_table_load(engine, df, table, mode)
    with engine.begin() as conn:
        if mode == "replace":
            conn.execute(text(f"TRUNCATE TABLE {_qualified_name(UPLOAD_SCHEMA, table)}"))
        df.to_sql(
            table,
            conn,
            schema=UPLOAD_SCHEMA,
            if_exists="fail" if mode == "create" else "append",
            index=False,
            chunksize=5000,
        )
    return null_columns


def _safe_script_stem(table: str, requested: str | None) -> str:
    if requested:
        stem = requested.strip().lower()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        stem = f"import_{table}_{stamp}"
    stem = re.sub(r"[^a-z0-9_]+", "_", stem).strip("_")
    if not stem or not stem[0].isalpha():
        stem = f"import_{stem or table}"
    stem = stem[:90].rstrip("_")
    if not SCRIPT_NAME_RE.match(stem):
        raise HTTPException(400, "Script name must start with a letter and use lowercase letters, numbers, or underscores.")
    return stem


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    path = directory / f"{stem}{suffix}"
    counter = 2
    while path.exists():
        path = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return path


def _render_import_script(
    source_file: Path,
    table: str,
    mode: str,
    materialized_views: list[dict],
    prefect_schedule: dict,
    flow_name: str,
) -> str:
    mv_sql = [
        f"REFRESH MATERIALIZED VIEW {_qualified_name(v['schema'], v['name'])}"
        for v in materialized_views
    ]
    target_sql = f"INSERT INTO {_qualified_name(UPLOAD_SCHEMA, table)}"
    script = textwrap.dedent(
        r'''
        """Generated data import flow.

        This script loads a CSV or Excel file into PostgreSQL using the same
        behavior as the Data Governance Import Data tool. It can run directly,
        or it can be served as a Prefect deployment with --serve.
        """

        import argparse
        import json
        import os
        import re
        from pathlib import Path

        try:
            from prefect import flow, task
            from prefect.schedules import Cron
            PREFECT_AVAILABLE = True
        except ImportError:
            Cron = None
            PREFECT_AVAILABLE = False

            def _identity_decorator(*decorator_args, **decorator_kwargs):
                if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:
                    return decorator_args[0]

                def decorate(fn):
                    return fn

                return decorate

            flow = _identity_decorator
            task = _identity_decorator


        DEFAULT_SOURCE_FILE = __DEFAULT_SOURCE_FILE__
        DEFAULT_TARGET_SCHEMA = __DEFAULT_TARGET_SCHEMA__
        DEFAULT_TARGET_TABLE = __DEFAULT_TARGET_TABLE__
        DEFAULT_MODE = __DEFAULT_MODE__
        DEFAULT_FLOW_NAME = __DEFAULT_FLOW_NAME__
        DEFAULT_DEPLOYMENT_NAME = __DEFAULT_DEPLOYMENT_NAME__
        DEFAULT_MATERIALIZED_VIEWS = __DEFAULT_MATERIALIZED_VIEWS__
        DEFAULT_PREFECT_SCHEDULE = __DEFAULT_PREFECT_SCHEDULE__

        # These static SQL strings make lineage scanners aware of table writes.
        DEFAULT_TARGET_TABLE_SQL = __DEFAULT_TARGET_TABLE_SQL__
        DEFAULT_MATERIALIZED_VIEW_SQL = __DEFAULT_MATERIALIZED_VIEW_SQL__

        target_schema = DEFAULT_TARGET_SCHEMA
        target_table = DEFAULT_TARGET_TABLE
        IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


        def require_identifier(value: str, label: str) -> str:
            value = (value or "").strip()
            if not IDENTIFIER_RE.fullmatch(value):
                raise ValueError(f"Invalid {label}: {value!r}")
            return value


        def quote_ident(value: str) -> str:
            value = require_identifier(value, "SQL identifier")
            return '"' + value.replace('"', '""') + '"'


        def qualified_name(schema: str, table_name: str) -> str:
            return f"{quote_ident(schema)}.{quote_ident(table_name)}"


        def create_engine_from_env():
            from sqlalchemy import create_engine
            from sqlalchemy.engine import URL
            from sqlalchemy.pool import NullPool

            host = os.getenv("DG_UPLOAD_PGHOST") or os.getenv("PGHOST")
            port = os.getenv("DG_UPLOAD_PGPORT") or os.getenv("PGPORT") or "5432"
            database = os.getenv("DG_UPLOAD_PGDATABASE") or os.getenv("PGDATABASE") or "postgres"
            user = os.getenv("DG_UPLOAD_PGUSER")
            password = os.getenv("DG_UPLOAD_PGPASSWORD")
            missing = []
            if not host:
                missing.append("PGHOST or DG_UPLOAD_PGHOST")
            if not database:
                missing.append("PGDATABASE or DG_UPLOAD_PGDATABASE")
            if not user:
                missing.append("DG_UPLOAD_PGUSER")
            if not password:
                missing.append("DG_UPLOAD_PGPASSWORD")
            if missing:
                raise RuntimeError("Missing import DB environment variables: " + ", ".join(missing))

            url = URL.create(
                "postgresql+psycopg2",
                username=user,
                password=password,
                host=host,
                port=int(port),
                database=database,
                query={"connect_timeout": "10"},
            )
            return create_engine(url, poolclass=NullPool, pool_pre_ping=True)


        def read_dataframe(source_file: str):
            import pandas as pd

            path = Path(source_file).expanduser()
            ext = path.suffix.lower()
            if ext == ".csv":
                df = None
                for enc in ("utf-8", "cp1252", "latin-1"):
                    try:
                        df = pd.read_csv(path, sep=None, engine="python", encoding=enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if df is None:
                    raise ValueError("Could not decode the CSV file.")
            elif ext in (".xlsx", ".xls"):
                df = pd.read_excel(path)
            else:
                raise ValueError(f"Unsupported file type: {ext} (use .csv, .xlsx or .xls)")

            if df.empty:
                raise ValueError("The file has no rows.")

            seen = {}
            columns = []
            for i, column in enumerate(df.columns):
                cleaned = re.sub(r"\W+", "_", str(column).strip()).strip("_").lower() or f"col_{i}"
                seen[cleaned] = seen.get(cleaned, 0) + 1
                columns.append(cleaned if seen[cleaned] == 1 else f"{cleaned}_{seen[cleaned]}")
            df.columns = columns
            return df


        def existing_tables(engine, schema: str) -> set[str]:
            from sqlalchemy import text

            q = text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            )
            with engine.connect() as conn:
                return {row[0] for row in conn.execute(q, {"s": schema})}


        def write_df_to_pg(df, target_schema: str, target_table: str, mode: str) -> dict:
            from sqlalchemy import text

            target_schema = require_identifier(target_schema, "target schema")
            target_table = require_identifier(target_table, "target table")
            mode = (mode or "").strip().lower()
            if mode not in ("append", "replace"):
                raise ValueError(f"Invalid mode: {mode}")

            engine = create_engine_from_env()
            try:
                existing = existing_tables(engine, target_schema)
                if target_table not in existing:
                    raise ValueError(f"{target_schema}.{target_table} does not exist. Create the table once before scheduling imports.")

                null_columns = []
                q = text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                )
                with engine.connect() as conn:
                    table_cols = {row[0] for row in conn.execute(q, {"s": target_schema, "t": target_table})}
                extra = [column for column in df.columns if column not in table_cols]
                if extra:
                    raise ValueError(
                        f"The file has columns that do not exist in {target_schema}.{target_table}: "
                        + ", ".join(extra)
                    )
                null_columns = sorted(table_cols - set(df.columns))

                with engine.begin() as conn:
                    if mode == "replace":
                        conn.execute(text(f"TRUNCATE TABLE {qualified_name(target_schema, target_table)}"))
                    df.to_sql(
                        target_table,
                        conn,
                        schema=target_schema,
                        if_exists="append",
                        index=False,
                        chunksize=5000,
                    )
                return {"rows": int(len(df)), "null_columns": null_columns}
            finally:
                engine.dispose()


        def parse_materialized_view(value: str) -> dict:
            if "." not in value:
                raise ValueError(f"Materialized view must be schema.name: {value}")
            schema, name = value.split(".", 1)
            return {
                "schema": require_identifier(schema, "materialized view schema"),
                "name": require_identifier(name, "materialized view name"),
            }


        def refresh_materialized_views(materialized_views: list[dict]) -> list[str]:
            if not materialized_views:
                return []

            from sqlalchemy import text

            engine = create_engine_from_env()
            refreshed = []
            try:
                with engine.begin() as conn:
                    for view in materialized_views:
                        schema = require_identifier(view["schema"], "materialized view schema")
                        name = require_identifier(view["name"], "materialized view name")
                        conn.execute(text(f"REFRESH MATERIALIZED VIEW {qualified_name(schema, name)}"))
                        refreshed.append(f"{schema}.{name}")
                return refreshed
            finally:
                engine.dispose()


        def build_prefect_cron_schedule(cron: str | None, timezone: str | None):
            if not cron:
                return None
            if Cron is None:
                raise RuntimeError("Prefect is required to serve a scheduled deployment.")
            return Cron(cron, timezone=timezone or None, slug="import-data-schedule")


        @task(retries=1, retry_delay_seconds=30)
        def import_file_task(source_file: str, target_schema: str, target_table: str, mode: str) -> dict:
            df = read_dataframe(source_file)
            result = write_df_to_pg(df, target_schema, target_table, mode)
            result.update({
                "source_file": source_file,
                "schema": target_schema,
                "table": target_table,
                "mode": mode,
            })
            return result


        @task(retries=1, retry_delay_seconds=30)
        def refresh_materialized_views_task(materialized_views: list[dict]) -> list[str]:
            return refresh_materialized_views(materialized_views)


        @flow(name=DEFAULT_FLOW_NAME, log_prints=True)
        def import_data_flow(
            source_file: str = DEFAULT_SOURCE_FILE,
            target_schema: str = DEFAULT_TARGET_SCHEMA,
            target_table: str = DEFAULT_TARGET_TABLE,
            mode: str = DEFAULT_MODE,
            materialized_views: list[dict] | None = None,
        ) -> dict:
            selected_views = DEFAULT_MATERIALIZED_VIEWS if materialized_views is None else materialized_views
            import_result = import_file_task(source_file, target_schema, target_table, mode)
            refreshed = refresh_materialized_views_task(selected_views) if selected_views else []
            return {
                "import": import_result,
                "materialized_views_refreshed": refreshed,
            }


        def main():
            parser = argparse.ArgumentParser(description="Load a CSV/Excel file into PostgreSQL.")
            parser.add_argument("--source-file", default=DEFAULT_SOURCE_FILE)
            parser.add_argument("--schema", default=DEFAULT_TARGET_SCHEMA)
            parser.add_argument("--table", default=DEFAULT_TARGET_TABLE)
            parser.add_argument("--mode", choices=("append", "replace"), default=DEFAULT_MODE)
            parser.add_argument(
                "--refresh-materialized-view",
                action="append",
                default=None,
                metavar="schema.name",
                help="Materialized view to refresh after loading. Repeat for multiple views.",
            )
            parser.add_argument("--serve", action="store_true", help="Create and serve a Prefect deployment.")
            parser.add_argument("--deployment-name", default=DEFAULT_DEPLOYMENT_NAME)
            parser.add_argument("--cron", help="Override the embedded Prefect cron schedule for --serve, for example '0 8 * * *'.")
            parser.add_argument("--timezone", help="Override the embedded Prefect schedule timezone for --serve.")
            parser.add_argument("--interval", type=int, help="Override the embedded schedule with an interval in seconds for --serve.")
            parser.add_argument("--no-schedule", action="store_true", help="Serve a manual deployment without an automatic schedule.")
            args = parser.parse_args()

            if args.cron and args.interval:
                raise SystemExit("Use either --cron or --interval, not both.")

            if args.refresh_materialized_view is None:
                materialized_views = DEFAULT_MATERIALIZED_VIEWS
            else:
                materialized_views = [parse_materialized_view(v) for v in args.refresh_materialized_view]

            parameters = {
                "source_file": args.source_file,
                "target_schema": args.schema,
                "target_table": args.table,
                "mode": args.mode,
                "materialized_views": materialized_views,
            }

            if args.serve:
                if not PREFECT_AVAILABLE:
                    raise SystemExit("Prefect is required for --serve. Install prefect in this Python environment.")
                serve_kwargs = {
                    "name": args.deployment_name,
                    "tags": ["data-governance", "import-data"],
                    "parameters": parameters,
                    "pause_on_shutdown": False,
                }
                if args.no_schedule:
                    pass
                elif args.interval:
                    serve_kwargs["interval"] = args.interval
                else:
                    schedule_cron = args.cron if args.cron is not None else DEFAULT_PREFECT_SCHEDULE.get("cron")
                    schedule_timezone = args.timezone if args.timezone is not None else DEFAULT_PREFECT_SCHEDULE.get("timezone")
                    schedule = build_prefect_cron_schedule(schedule_cron, schedule_timezone)
                    if schedule is not None:
                        serve_kwargs["schedules"] = [schedule]
                import_data_flow.serve(**serve_kwargs)
                return

            result = import_data_flow(**parameters)
            print(json.dumps(result, indent=2, default=str))


        if __name__ == "__main__":
            main()
        '''
    ).lstrip()

    replacements = {
        "__DEFAULT_SOURCE_FILE__": json.dumps(str(source_file)),
        "__DEFAULT_TARGET_SCHEMA__": json.dumps(UPLOAD_SCHEMA),
        "__DEFAULT_TARGET_TABLE__": json.dumps(table),
        "__DEFAULT_MODE__": json.dumps(mode),
        "__DEFAULT_FLOW_NAME__": json.dumps(flow_name),
        "__DEFAULT_DEPLOYMENT_NAME__": json.dumps(flow_name),
        "__DEFAULT_TARGET_TABLE_SQL__": json.dumps(target_sql),
        "__DEFAULT_MATERIALIZED_VIEWS__": json.dumps(
            [{"schema": v["schema"], "name": v["name"]} for v in materialized_views],
            indent=4,
        ),
        "__DEFAULT_PREFECT_SCHEDULE__": json.dumps(prefect_schedule, indent=4),
        "__DEFAULT_MATERIALIZED_VIEW_SQL__": json.dumps(mv_sql, indent=4),
    }
    for needle, value in replacements.items():
        script = script.replace(needle, value)
    return script


def _create_import_script(req: GenerateScriptRequest, staged: Path, df) -> dict:
    mode = _clean_mode(req.mode)
    if mode == "create":
        raise HTTPException(400, "Create the table once first, then generate an append or replace script for recurring imports.")
    table = _clean_table_name(req.table)
    prefect_schedule = _clean_prefect_schedule(req.schedule)
    engine = _get_engine()
    try:
        materialized_views = _validate_materialized_views(engine, req.materialized_views)
        null_columns = _validate_table_load(engine, df, table, mode)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Import script validation failed for %s.%s: %s", UPLOAD_SCHEMA, table, e)
        raise HTTPException(502, _postgres_error("Could not validate import script", e))
    finally:
        engine.dispose()

    script_dir = _current_script_dir()
    data_dir = script_dir / "data"
    script_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    original_name = staged.name.split("__", 1)[-1]
    original_stem = Path(original_name).stem or "upload"
    original_suffix = Path(original_name).suffix or ".csv"
    data_path = _unique_path(data_dir, f"{uuid.uuid4().hex}_{original_stem}", original_suffix)
    shutil.copy2(staged, data_path)

    script_stem = _safe_script_stem(table, req.script_name)
    script_path = _unique_path(script_dir, script_stem, ".py")
    flow_name = script_stem.replace("_", "-")
    script_path.write_text(
        _render_import_script(data_path, table, mode, materialized_views, prefect_schedule, flow_name),
        encoding="utf-8",
    )

    selected_view_names = [v["display_name"] for v in materialized_views]
    return {
        "script_path": str(script_path),
        "input_path": str(data_path),
        "entrypoint": f"{script_path}:import_data_flow",
        "flow_name": flow_name,
        "schema": UPLOAD_SCHEMA,
        "table": table,
        "mode": mode,
        "rows": int(len(df)),
        "null_columns": null_columns,
        "materialized_views": selected_view_names,
        "schedule": prefect_schedule,
        "run_command": f'python "{script_path}"',
        "serve_command": f'python "{script_path}" --serve',
    }


@router.get("/status")
def import_status():
    """Config state for the Import Data section, without secrets."""
    missing = _missing_config()
    deps_ok = True
    try:
        import pandas  # noqa: F401
        import sqlalchemy  # noqa: F401
    except ImportError:
        deps_ok = False
    return {
        "configured": not missing and deps_ok,
        "missing": missing,
        "dependencies_installed": deps_ok,
        "host": UPLOAD_PGHOST,
        "database": UPLOAD_PGDATABASE,
        "schema": UPLOAD_SCHEMA,
        "script_dir": str(_current_script_dir()),
    }


@router.put("/script-dir")
def update_script_dir(req: ScriptDirRequest, request: Request):
    """Persist the default folder for generated import scripts."""
    script_dir = _resolve_script_dir(req.path)
    try:
        script_dir.mkdir(parents=True, exist_ok=True)
        data_dir = script_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"Could not create script folder: {e}")

    set_setting(SCRIPT_DIR_SETTING, str(script_dir))
    with get_db() as db:
        log_event(
            db, "app_settings", None, "Import script folder",
            "updated", str(script_dir), get_actor(request),
        )
    return {"script_dir": str(script_dir)}


@router.get("/tables")
def list_target_tables():
    """Tables in the target schema."""
    engine = _get_engine()
    try:
        return {"schema": UPLOAD_SCHEMA, "tables": _existing_tables(engine)}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Import tables listing failed: %s", e)
        raise HTTPException(502, _postgres_error("Could not query Postgres", e))
    finally:
        engine.dispose()


@router.get("/materialized-views")
def list_materialized_views():
    """Materialized views available to refresh after an import."""
    engine = _get_engine()
    try:
        return {"views": _materialized_views(engine)}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Import materialized view listing failed: %s", e)
        raise HTTPException(502, _postgres_error("Could not query materialized views", e))
    finally:
        engine.dispose()


@router.post("/materialized-views/refresh")
def refresh_materialized_views(req: RefreshMaterializedViewsRequest, request: Request):
    """Refresh selected materialized views immediately."""
    engine = _get_engine()
    try:
        views = _validate_materialized_views(engine, req.materialized_views)
        if not views:
            raise HTTPException(400, "Choose at least one materialized view to refresh.")
        from app.routers.pipelines import assert_resource_unlocked
        with get_db() as db:
            for view in views:
                resource_key = "|".join(
                    [normalize_server(UPLOAD_PGHOST), UPLOAD_PGDATABASE, view["schema"], view["name"]]
                )
                assert_resource_unlocked(db, "mv", resource_key)
        refreshed = _refresh_materialized_views(engine, views)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Materialized view refresh failed: %s", e)
        raise HTTPException(502, _postgres_error("Materialized view refresh failed", e))
    finally:
        engine.dispose()

    with get_db() as db:
        log_event(
            db, "data_import", None, None,
            "refresh-materialized-views",
            ", ".join(refreshed),
            get_actor(request),
        )
    return {"refreshed": refreshed}


@router.post("/preview")
async def preview_file(file: UploadFile = File(...)):
    """Stage an uploaded file and return its parsed shape."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext or '(none)'} (use .csv, .xlsx or .xls)")

    STAGING_DIR.mkdir(exist_ok=True)
    _purge_stale_staging()

    token = uuid.uuid4().hex
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or f"upload{ext}")
    staged = STAGING_DIR / f"{token}__{safe_name}"
    staged.write_bytes(await file.read())

    try:
        df = _read_dataframe(staged, safe_name)
    except HTTPException:
        staged.unlink(missing_ok=True)
        raise

    sample = df.head(5).fillna("").astype(str).values.tolist()
    return {
        "token": token,
        "filename": file.filename,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "sample": sample,
    }


@router.post("/script")
def generate_import_script(req: GenerateScriptRequest, request: Request):
    """Create a Prefect-compatible Python script for the staged import."""
    staged = _staged_file(req.token)
    df = _read_dataframe(staged, staged.name)
    result = _create_import_script(req, staged, df)

    with get_db() as db:
        log_event(
            db, "data_import", None, f"{result['schema']}.{result['table']}",
            "script-created",
            f"{result['script_path']} ({result['rows']} rows)"
            + (f"; refreshes {', '.join(result['materialized_views'])}" if result["materialized_views"] else ""),
            get_actor(request),
        )
    return result


@router.post("/load")
def load_file(req: LoadRequest, request: Request):
    """Write a staged file into the target table."""
    mode = _clean_mode(req.mode)
    table = _clean_table_name(req.table)

    staged = _staged_file(req.token)
    df = _read_dataframe(staged, staged.name)
    engine = _get_engine()
    table_loaded = False
    try:
        try:
            materialized_views = _validate_materialized_views(engine, req.materialized_views)
            null_columns = _write_dataframe_to_table(engine, df, table, mode)
            table_loaded = True
        except HTTPException:
            raise
    except Exception as e:
        logger.warning("Import load failed for %s.%s: %s", UPLOAD_SCHEMA, table, e)
        raise HTTPException(502, _postgres_error("Load failed, nothing was committed", e))
        try:
            refreshed_views = _refresh_materialized_views(engine, materialized_views)
        except Exception as e:
            logger.warning("Materialized view refresh failed after import for %s.%s: %s", UPLOAD_SCHEMA, table, e)
            raise HTTPException(
                502,
                _postgres_error(f"Data loaded into {UPLOAD_SCHEMA}.{table}, but materialized view refresh failed", e),
            )
    finally:
        engine.dispose()
        if table_loaded and mode != "create":
            staged.unlink(missing_ok=True)

    with get_db() as db:
        log_event(
            db, "data_import", None, f"{UPLOAD_SCHEMA}.{table}",
            f"import-{mode}",
            f"{len(df)} rows from {staged.name.split('__', 1)[-1]}"
            + (f"; refreshed {', '.join(refreshed_views)}" if refreshed_views else ""),
            get_actor(request),
        )
    return {
        "schema": UPLOAD_SCHEMA,
        "table": table,
        "mode": mode,
        "rows": int(len(df)),
        "null_columns": null_columns,
        "refreshed_materialized_views": refreshed_views,
    }
