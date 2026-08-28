"""Manual PostgreSQL materialized-view refresh used by Lineage/Pipelines."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app import flow_sql
from app.config import UPLOAD_PGHOST, UPLOAD_PGPORT
from app.database import get_db
from app.routers.eventlog import get_actor, log_event
from app.source_identity import normalize_server, postgres_server_identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materialized-views", tags=["materialized-views"])


def _materialized_view_identity(source_id: int) -> dict:
    """Resolve one active source to its canonical PostgreSQL identity."""
    with get_db() as db:
        row = db.execute(
            """SELECT s.id AS source_id, s.name AS source_name,
                      spi.server_name, spi.database_name, spi.schema_name,
                      spi.relation_name, spi.relation_kind
               FROM sources s
               JOIN source_postgres_identities spi ON spi.source_id=s.id
               WHERE s.id=? AND COALESCE(s.archived, 0)=0""",
            (int(source_id),),
        ).fetchone()

    if row is None:
        raise HTTPException(
            404,
            "This source has no active canonical PostgreSQL identity. Run the PostgreSQL dependency scan first.",
        )
    if row["relation_kind"] != "materialized_view":
        raise HTTPException(400, "The selected source is not a materialized view.")
    if normalize_server(row["server_name"]) != postgres_server_identity(
        UPLOAD_PGHOST,
        UPLOAD_PGPORT,
    ):
        raise HTTPException(
            400,
            "This materialized view is on a different PostgreSQL server than the configured Flow SQL connection.",
        )
    return dict(row)


def _verify_materialized_view(engine, identity: dict) -> None:
    """Confirm the canonical identity still exists in its recorded database."""
    from sqlalchemy import text

    query = text(
        "SELECT 1 FROM pg_matviews "
        "WHERE schemaname=:schema AND matviewname=:relation LIMIT 1"
    )
    with engine.connect() as connection:
        found = connection.execute(
            query,
            {
                "schema": identity["schema_name"],
                "relation": identity["relation_name"],
            },
        ).fetchone()
    if found is None:
        raise HTTPException(
            409,
            "The materialized view no longer matches the last PostgreSQL scan. Run the dependency scan again before refreshing it.",
        )


def _refresh_materialized_view(engine, identity: dict) -> str:
    from sqlalchemy import text

    qualified_name = (
        f"{flow_sql._quote_identifier(identity['schema_name'])}."
        f"{flow_sql._quote_identifier(identity['relation_name'])}"
    )
    with engine.begin() as connection:
        connection.execute(text(f"REFRESH MATERIALIZED VIEW {qualified_name}"))
    return f"{identity['database_name']}.{identity['schema_name']}.{identity['relation_name']}"


def _postgres_error(action: str, exc: Exception) -> str:
    message = flow_sql._database_error(exc)
    lower = message.lower()
    if "remaining connection slots are reserved" in lower or "too many clients already" in lower:
        return (
            f"{action}: PostgreSQL has no free connection slots for this role. "
            "Close idle database sessions or raise the database connection limit, then retry."
        )
    return f"{action}: {message}"


@router.post("/{source_id}/refresh")
def refresh_materialized_view(source_id: int, request: Request):
    """Refresh one source by its exact, database-aware PostgreSQL identity."""
    identity = _materialized_view_identity(source_id)

    from app.routers.pipelines import assert_resource_unlocked

    resource_key = "|".join(
        [
            normalize_server(identity["server_name"]),
            identity["database_name"],
            identity["schema_name"],
            identity["relation_name"],
        ]
    )
    with get_db() as db:
        assert_resource_unlocked(db, "mv", resource_key)

    try:
        engine = flow_sql._engine(identity["database_name"])
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    try:
        _verify_materialized_view(engine, identity)
        refreshed = _refresh_materialized_view(engine, identity)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Materialized-view refresh failed for source %s: %s", source_id, exc)
        raise HTTPException(
            502, _postgres_error("Materialized view refresh failed", exc)
        )
    finally:
        engine.dispose()

    with get_db() as db:
        log_event(
            db,
            "source",
            source_id,
            identity["source_name"],
            "refresh-materialized-view",
            refreshed,
            get_actor(request),
        )
    return {"refreshed": refreshed, "source_id": source_id}
