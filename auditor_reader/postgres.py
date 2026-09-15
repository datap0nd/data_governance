"""Fixed aggregate queries. No caller-supplied SQL, expressions, or identifiers."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from .policy import InspectionError, digest
from .profiles import category


# Deliberately conservative: no role memberships, writable objects, PUBLIC
# TEMP/CREATE, user functions, sequence authority, or administrator powers.
PRIVILEGES = """
SELECT
 current_user <> session_user,
 EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=current_user
         AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)),
 EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname<>current_user
         AND pg_catalog.pg_has_role(current_user, oid, 'MEMBER')),
 EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname=current_database()
         AND (datdba=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
          OR pg_catalog.has_database_privilege(current_user, oid, 'CREATE')
          OR pg_catalog.has_database_privilege(current_user, oid, 'TEMP'))),
 EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
         WHERE pg_catalog.has_schema_privilege(current_user, oid, 'CREATE')),
 EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND
           (c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
            OR (c.relkind IN ('r','p','v','m','f') AND
                (pg_catalog.has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                 OR pg_catalog.has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')))
            OR (c.relkind='S' AND pg_catalog.has_sequence_privilege(current_user,c.oid,'USAGE,UPDATE')))),
 EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
         AND pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE'))
"""

CATALOG = """
SELECT c.oid, c.relowner, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
       c.relhasrules, am.amname,
       EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent=c.oid OR i.inhrelid=c.oid),
       a.attname, a.atttypid, a.atttypmod, a.attnotnull, a.attcollation,
       t.typnamespace=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname='pg_catalog'),
       t.typname, a.attgenerated
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam
WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

SAFE_TYPES = {"text", "varchar", "bpchar", "int2", "int4", "int8", "numeric", "float4", "float8", "date", "timestamp", "timestamptz", "bool", "uuid"}

INDEXES = """SELECT i.indexrelid, c.relowner, am.amname, i.indexprs IS NOT NULL,
 i.indpred IS NOT NULL, i.indkey::text, i.indclass::text, i.indcollation::text,
 EXISTS(SELECT 1 FROM pg_catalog.unnest(i.indclass::oid[]) op(oid)
        JOIN pg_catalog.pg_opclass oc ON oc.oid=op.oid
        JOIN pg_catalog.pg_namespace n ON n.oid=oc.opcnamespace WHERE n.nspname<>'pg_catalog')
 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
 JOIN pg_catalog.pg_am am ON am.oid=c.relam WHERE i.indrelid=%s ORDER BY i.indexrelid"""


def verify_privileges(cursor):
    cursor.execute(PRIVILEGES)
    if any(cursor.fetchone()):
        raise InspectionError("reader_privileges_unsafe")


def definition(cursor, dataset):
    cursor.execute(CATALOG, (dataset.schema_name, dataset.table_name))
    rows = cursor.fetchall()
    if not rows:
        raise InspectionError("sql_target_missing")
    for row in rows:
        if row[2] != "r" or any(row[3:6]) or row[6] != "heap" or row[7] or not row[13] or row[14] not in SAFE_TYPES or row[15]:
            raise InspectionError("sql_target_definition_unsafe")
    by_name = {row[8]: row for row in rows}
    if any((c.sql_name or c.name) not in by_name for c in dataset.columns):
        raise InspectionError("approved_column_missing")
    cursor.execute(INDEXES, (rows[0][0],))
    indexes = cursor.fetchall()
    if any(row[2] not in {"btree", "hash"} or row[3] or row[4] or row[8] for row in indexes):
        raise InspectionError("sql_index_definition_unsafe")
    return digest([cursor.connection.info.dbname, rows, indexes]), by_name


@contextmanager
def connection(dsn, cancelled):
    import psycopg2
    if not dsn:
        raise InspectionError("sql_reader_not_configured")
    parameters = psycopg2.extensions.parse_dsn(dsn)
    if any(not parameters.get(key) for key in ("host", "port", "dbname", "user", "password")) or any(key in parameters for key in ("service", "passfile", "options")):
        raise InspectionError("explicit_reader_credentials_required")
    if parameters["host"] not in {"localhost", "127.0.0.1", "::1"} and parameters.get("sslmode") != "verify-full":
        raise InspectionError("verified_database_tls_required")
    # No application config imports, uploader credentials, DSN fallback, or pool.
    parameters.update(connect_timeout=5, application_name="metronome-audit-reader",
        options="-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=1000 -c search_path=pg_catalog -c idle_in_transaction_session_timeout=35000")
    conn = psycopg2.connect(**parameters)
    finished = threading.Event()

    def monitor():
        while not finished.wait(0.1):
            if cancelled():
                try:
                    conn.cancel()
                except psycopg2.Error:
                    pass
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL search_path = pg_catalog")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SET LOCAL lock_timeout = '1s'")
            cursor.execute("SET LOCAL work_mem = '4MB'")
            cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            verify_privileges(cursor)
            if cancelled():
                raise InspectionError("cancelled")
            yield cursor
    finally:
        finished.set()
        # There is no commit path, including for inspection errors.
        try:
            conn.rollback()
        finally:
            conn.close()
            watcher.join(timeout=1)


def profile_sql(dataset, dsn, salt, cancelled, *, discover=False):
    from psycopg2 import sql
    external_cancelled = cancelled
    deadline = time.monotonic() + 30
    cancelled = lambda: external_cancelled() or time.monotonic() >= deadline
    with connection(dsn, cancelled) as cursor:
        fingerprint, _ = definition(cursor, dataset)
        target = sql.Identifier(dataset.schema_name, dataset.table_name)
        # Hold AccessShare through definition recheck and the entire profile;
        # DDL cannot swap in a view/function between validation and SELECT.
        cursor.execute(sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(target))
        locked_fingerprint, columns = definition(cursor, dataset)
        if locked_fingerprint != fingerprint:
            raise InspectionError("sql_target_changed")
        if discover:
            return {"fingerprint": fingerprint}
        if dataset.fingerprint and fingerprint != dataset.fingerprint:
            raise InspectionError("sql_definition_not_approved")
        expressions = [sql.SQL("pg_catalog.count(*)")]
        for column in dataset.columns:
            col = sql.Identifier(column.sql_name or column.name)
            # Cast only catalog-approved built-in scalar types.
            value = sql.SQL("NULLIF({}::pg_catalog.text, '')").format(col)
            expressions.extend([
                sql.SQL("pg_catalog.count(*) FILTER (WHERE {} IS NULL)").format(value),
                sql.SQL("pg_catalog.count(DISTINCT {})").format(value),
            ])
        cursor.execute(sql.SQL("SELECT {} FROM ONLY {}").format(sql.SQL(", ").join(expressions), target))
        counts = cursor.fetchone()
        cursor.execute("SELECT pg_catalog.pg_is_in_recovery()")
        in_recovery = cursor.fetchone()[0]
        result = {"rows": int(counts[0]), "complete": True, "schema": fingerprint, "columns": {},
                  "database": cursor.connection.info.dbname, "in_recovery": in_recovery}
        for index, column in enumerate(dataset.columns):
            if cancelled():
                raise InspectionError("cancelled")
            col = sql.Identifier(column.sql_name or column.name)
            value = sql.SQL("NULLIF({}::pg_catalog.text, '')").format(col)
            item = {"kind": column.kind, "nulls": int(counts[1 + index * 2]), "distinct": int(counts[2 + index * 2]),
                    "invalid": 0, "sum": None, "min": None, "max": None}
            if column.kind == "number":
                # No arbitrary casts or permissive locale conversion. Text with
                # currency/commas is unknown, not silently treated as zero.
                valid = sql.SQL("({} ~ '^[+-]?[0-9]{{1,30}}([.][0-9]{{1,20}})?$')").format(value)
                numeric = sql.SQL("CASE WHEN {} THEN {}::pg_catalog.numeric ELSE NULL END").format(valid, value)
                cursor.execute(sql.SQL("SELECT pg_catalog.count(*) FILTER (WHERE {} IS NOT NULL AND NOT {}), pg_catalog.sum({}), pg_catalog.min({}), pg_catalog.max({}) FROM ONLY {}").format(value, valid, numeric, numeric, numeric, target))
                invalid, total, minimum, maximum = cursor.fetchone()
                item.update(invalid=int(invalid), sum=str(total or 0) if not invalid else None,
                            min=str(minimum) if minimum is not None else None,
                            max=str(maximum) if maximum is not None else None)
            elif column.kind == "date" and columns[column.sql_name or column.name][14] == "date":
                cursor.execute(sql.SQL("SELECT pg_catalog.min({}), pg_catalog.max({}) FROM ONLY {}").format(col, col, target))
                minimum, maximum = cursor.fetchone()
                item.update(min=str(minimum) if minimum is not None else None,
                            max=str(maximum) if maximum is not None else None)
            if item["distinct"] <= 256:
                bounded_value = sql.SQL("CASE WHEN pg_catalog.octet_length({})<=4096 THEN {} ELSE NULL END").format(value, value)
                cursor.execute(sql.SQL("SELECT {}, pg_catalog.count(*) FROM ONLY {} WHERE {} IS NOT NULL GROUP BY {} LIMIT 257").format(bounded_value, target, value, value))
                groups = cursor.fetchall()
                # Reject giant strings rather than send them to a model.
                if any(key is None for key, _ in groups):
                    raise InspectionError("sql_result_budget")
                item.update(groups={category(str(key), salt): int(n) for key, n in groups}, groups_complete=len(groups) <= 256)
            else:
                item.update(groups={}, groups_complete=False)
            result["columns"][column.name] = item
        if cancelled():
            raise InspectionError("cancelled")
        return result
