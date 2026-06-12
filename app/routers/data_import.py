"""Import Data - load a CSV/Excel file into a PostgreSQL table.

Two-step flow: POST /preview parses the uploaded file and stages it under a
token; POST /load writes the staged file into the target table (create new,
append, or replace). Replace runs TRUNCATE + INSERT in one transaction so a
failed load leaves the previous data intact.

Writes use the dedicated DG_UPLOAD_* credentials from config, never the
read-only probing credentials (PGUSER/PGPASSWORD).
"""

import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.config import (
    UPLOAD_PGDATABASE,
    UPLOAD_PGHOST,
    UPLOAD_PGPASSWORD,
    UPLOAD_PGPORT,
    UPLOAD_PGUSER,
    UPLOAD_SCHEMA,
)
from app.database import get_db
from app.routers.eventlog import get_actor, log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data-import", tags=["data-import"])

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
STAGING_DIR = Path(tempfile.gettempdir()) / "dg_data_import"
STAGING_MAX_AGE_SECONDS = 2 * 3600
ALLOWED_EXTENSIONS = (".csv", ".xlsx", ".xls")


class LoadRequest(BaseModel):
    token: str
    table: str
    mode: str  # append | replace | create


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
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
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
    return create_engine(url)


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
    }


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
        raise HTTPException(502, f"Could not query Postgres: {e}")
    finally:
        engine.dispose()


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


@router.post("/load")
def load_file(req: LoadRequest, request: Request):
    """Write a staged file into the target table."""
    if req.mode not in ("append", "replace", "create"):
        raise HTTPException(400, f"Invalid mode: {req.mode}")
    table = req.table.strip().lower()
    if not NAME_RE.match(table):
        raise HTTPException(400, f"Invalid table name: {table} (letters, numbers, underscores; start with a letter)")

    staged = _staged_file(req.token)
    df = _read_dataframe(staged, staged.name)
    engine = _get_engine()
    try:
        from sqlalchemy import text

        existing = set(_existing_tables(engine))
        if req.mode == "create" and table in existing:
            raise HTTPException(400, f"{UPLOAD_SCHEMA}.{table} already exists. Use append or replace.")
        if req.mode != "create" and table not in existing:
            raise HTTPException(400, f"{UPLOAD_SCHEMA}.{table} does not exist. Use create.")

        null_columns: list[str] = []
        if req.mode != "create":
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

        with engine.begin() as conn:
            if req.mode == "replace":
                conn.execute(text(f'TRUNCATE TABLE "{UPLOAD_SCHEMA}"."{table}"'))
            df.to_sql(
                table,
                conn,
                schema=UPLOAD_SCHEMA,
                if_exists="fail" if req.mode == "create" else "append",
                index=False,
                chunksize=5000,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Import load failed for %s.%s: %s", UPLOAD_SCHEMA, table, e)
        raise HTTPException(502, f"Load failed, nothing was committed: {e}")
    finally:
        engine.dispose()

    staged.unlink(missing_ok=True)
    with get_db() as db:
        log_event(
            db, "data_import", None, f"{UPLOAD_SCHEMA}.{table}",
            f"import-{req.mode}",
            f"{len(df)} rows from {staged.name.split('__', 1)[-1]}",
            get_actor(request),
        )
    return {
        "schema": UPLOAD_SCHEMA,
        "table": table,
        "mode": req.mode,
        "rows": int(len(df)),
        "null_columns": null_columns,
    }
