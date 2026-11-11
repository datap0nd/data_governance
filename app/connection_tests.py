"""The "Test connection" buttons on the Settings page.

Each test signs in with the saved values and reports in one plain sentence
what happened. The probe test only ever reads: it runs in a read-only session
and asks PostgreSQL what the account is allowed to do, without trying it.
"""
from __future__ import annotations

import re

from app import config


def _clean(error: Exception, *secrets: str) -> str:
    text = re.sub(r"\s+", " ", str(error)).strip() or type(error).__name__
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:400]


def probe() -> dict:
    """Sign in as the read-only probe account and check it really is read-only."""
    missing = [label for label, value in (("host", config.PGHOST), ("user", config.PGUSER),
                                          ("password", config.PGPASSWORD)) if not value]
    if missing:
        return {"ok": False, "message": f"Not tested: the probe {' and '.join(missing)} "
                                        f"{'is' if len(missing) == 1 else 'are'} not set."}
    connection = None
    try:
        import psycopg2

        connection = psycopg2.connect(
            host=config.PGHOST, port=int(config.PGPORT), user=config.PGUSER, password=config.PGPASSWORD,
            database=config.PGDATABASE, connect_timeout=10,
            options="-c statement_timeout=15000 -c lock_timeout=5000",
        )
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT current_user,
                          pg_catalog.has_database_privilege(current_database(), 'CREATE'),
                          COALESCE((SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user), FALSE)"""
            )
            user, can_create, superuser = cursor.fetchone()
    except Exception as exc:
        return {"ok": False, "message": f"Could not sign in to {config.PGDATABASE} on {config.PGHOST}:{config.PGPORT} "
                                        f"as {config.PGUSER}. {_clean(exc, config.PGPASSWORD)}"}
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    where = f"{config.PGDATABASE} on {config.PGHOST}"
    if superuser or can_create:
        power = "is a superuser" if superuser else "can create objects in this database"
        return {"ok": False, "message": f"Signed in to {where} as {user}, but this account {power}. "
                                        "The probe account must only be able to read; use a read-only role."}
    return {"ok": True, "message": f"Signed in to {where} as {user}. The account cannot create objects in this database."}


def upload() -> dict:
    """Sign in as the shared upload account."""
    from app import flow_sql

    status = flow_sql.configuration_status()
    if status["missing"]:
        return {"ok": False, "message": "Not tested: these are not set: " + ", ".join(status["missing"]) + "."}
    engine = None
    try:
        from sqlalchemy import text

        engine = flow_sql._engine(config.UPLOAD_PGDATABASE)
        with engine.connect() as connection:
            user = connection.execute(text("SELECT current_user")).scalar()
    except Exception as exc:
        return {"ok": False, "message": f"Could not sign in to {config.UPLOAD_PGDATABASE} on "
                                        f"{config.UPLOAD_PGHOST}:{config.UPLOAD_PGPORT} as {config.UPLOAD_PGUSER}. "
                                        f"{_clean(Exception(flow_sql._database_error(exc)), config.UPLOAD_PGPASSWORD)}"}
    finally:
        if engine is not None:
            engine.dispose()
    return {"ok": True, "message": f"Signed in to {config.UPLOAD_PGDATABASE} on {config.UPLOAD_PGHOST} as {user}."}


TESTS = {"probe": probe, "upload": upload}
