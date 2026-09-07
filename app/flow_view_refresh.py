"""Post-SQL materialized-view refresh shared by every Flow launch surface.

The executor runs only after PostgreSQL confirmed the SQL insertion commit.
Each view refreshes in its own transaction, so a failure keeps the committed
insertion and every earlier refresh; the frozen plan and per-view checkpoints
allow a refresh-only recovery without repeating download, transformation or
insertion. This module must stay portable: it depends on ``flow_sql`` and the
standard library only, so generated scripts embed the identical executor.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from app import flow_sql

CAPABILITY = 'post_sql_refresh_v1'
MODES = ('off', 'automatic', 'manual')
RETRY_JOB_TYPE = 'view_retry'
# Refresh backstops mirror the full-pipeline stage: wait for a lock at most ten
# minutes and let one refresh run up to an hour before giving up on it.
LOCK_TIMEOUT_MS = 600_000
STATEMENT_TIMEOUT_MS = 3_600_000


class ViewRefreshError(RuntimeError):
    """A refresh failed after SQL committed; ``results`` keeps the checkpoints."""

    def __init__(self, message: str, results: list[dict]):
        super().__init__(message)
        self.results = results


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, limit: int = 63) -> str:
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or '\x00' in value or len(value.encode('utf-8')) > limit:
        return ''
    return value


def normalize_view(value) -> dict | None:
    """Return an exact database/schema/name identity or None when unusable."""
    if not isinstance(value, dict):
        return None
    database = _text(value.get('database'))
    schema = _text(value.get('schema'))
    name = _text(value.get('name') or value.get('view') or value.get('relation'))
    if not (database and schema and name):
        return None
    return {'database': database, 'schema': schema, 'name': name}


def view_key(view: dict) -> str:
    return '|'.join((view['database'], view['schema'], view['name']))


def resource_key(server: str, view: dict) -> str:
    """Exact physical identity shared with full-pipeline ``mv`` reservations."""
    return '|'.join((server or '', view['database'], view['schema'], view['name']))


def label(view: dict) -> str:
    return f"{view['database']}.{view['schema']}.{view['name']}"


def normalize_config(value) -> dict:
    """Validate a Flow's saved configuration; unknown input means Off."""
    if not isinstance(value, dict):
        return {'mode': 'off', 'views': []}
    mode = str(value.get('mode') or 'off').strip().casefold()
    if mode not in MODES:
        raise ValueError('Refresh materialized views must be Off, Automatic or Manual.')
    views: list[dict] = []
    seen: set[str] = set()
    for item in value.get('views') or []:
        view = normalize_view(item)
        if view is None:
            raise ValueError('Each materialized view needs a database, schema and view name.')
        key = view_key(view)
        if key in seen:
            continue
        seen.add(key)
        views.append(view)
    if len(views) > 200:
        raise ValueError('Choose at most 200 materialized views.')
    if mode != 'manual':
        views = []
    if mode == 'manual' and not views:
        raise ValueError('Manual refresh needs at least one materialized view.')
    return {'mode': mode, 'views': views}


def plan_views(job: dict) -> list[dict]:
    plan = job.get('post_sql_refresh') or {}
    if plan.get('deferred_to_pipeline'):
        return []
    return [view for view in (plan.get('views') or []) if normalize_view(view)]


def qualified(view: dict) -> str:
    return f"{flow_sql._quote_identifier(view['schema'])}.{flow_sql._quote_identifier(view['name'])}"


def _group(views: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for view in views:
        grouped.setdefault(view['database'], []).append(view)
    return grouped


def inspect_views(views: list[dict], *, engine_factory=None) -> list[dict]:
    """Read-only PostgreSQL check: existence, relation kind and refresh permission.

    Returns one record per requested view with ``exists``, ``kind``,
    ``can_refresh`` and ``owner``. Nothing is modified.
    """
    from sqlalchemy import text
    factory = engine_factory or flow_sql._engine
    findings: dict[str, dict] = {}
    for database, items in _group(views).items():
        engine = factory(database)
        try:
            with engine.connect() as connection:
                try:
                    connection.execute(text('SET TRANSACTION READ ONLY'))
                except Exception:
                    pass
                for view in items:
                    row = connection.execute(text(
                        "SELECT c.relkind, pg_get_userbyid(c.relowner) AS owner, "
                        "pg_has_role(current_user, c.relowner, 'MEMBER') AS owner_member "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=:schema AND c.relname=:name"
                    ), {'schema': view['schema'], 'name': view['name']}).fetchone()
                    if row is None:
                        findings[view_key(view)] = {**view, 'exists': False, 'kind': None, 'can_refresh': False, 'owner': None}
                        continue
                    kind = {'m': 'materialized_view', 'v': 'view', 'r': 'table', 'p': 'table', 'f': 'foreign_table'}.get(str(row[0]), str(row[0]))
                    findings[view_key(view)] = {**view, 'exists': True, 'kind': kind, 'owner': row[1],
                                                'can_refresh': kind == 'materialized_view' and bool(row[2])}
        finally:
            engine.dispose()
    return [findings[view_key(view)] for view in views]


def verification_problems(findings: list[dict]) -> list[str]:
    problems = []
    for item in findings:
        name = label(item)
        if not item.get('exists'):
            problems.append(f'{name} does not exist on the configured SQL server.')
        elif item.get('kind') != 'materialized_view':
            problems.append(f"{name} is a {str(item.get('kind') or 'relation').replace('_', ' ')}, not a materialized view.")
        elif not item.get('can_refresh'):
            problems.append(f"The SQL account cannot refresh {name}; it is owned by {item.get('owner') or 'another role'}.")
    return problems


def verify_views(views: list[dict], *, engine_factory=None) -> list[dict]:
    findings = inspect_views(views, engine_factory=engine_factory)
    problems = verification_problems(findings)
    if problems:
        raise RuntimeError('Materialized view check failed before SQL insertion: ' + ' '.join(problems))
    return findings


def inspect_dependencies(database: str, *, engine_factory=None) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """Read-only (dependent, upstream) relation pairs among views in one database."""
    from sqlalchemy import text
    factory = engine_factory or flow_sql._engine
    engine = factory(database)
    edges = []
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT DISTINCT dn.nspname, dependent.relname, un.nspname, upstream.relname "
                "FROM pg_depend d "
                "JOIN pg_rewrite rw ON rw.oid=d.objid "
                "JOIN pg_class dependent ON dependent.oid=rw.ev_class "
                "JOIN pg_class upstream ON upstream.oid=d.refobjid "
                "JOIN pg_namespace dn ON dn.oid=dependent.relnamespace "
                "JOIN pg_namespace un ON un.oid=upstream.relnamespace "
                "WHERE d.classid='pg_rewrite'::regclass AND d.refclassid='pg_class'::regclass "
                "AND d.deptype='n' AND dependent.relkind IN ('m','v') AND upstream.relkind IN ('m','v','r','p','f') "
                "AND dependent.oid<>upstream.oid "
                "AND dn.nspname NOT IN ('pg_catalog','information_schema') "
                "AND un.nspname NOT IN ('pg_catalog','information_schema')"
            )).fetchall()
            edges = [((str(r[0]), str(r[1])), (str(r[2]), str(r[3]))) for r in rows]
    finally:
        engine.dispose()
    return edges


def order_views(views: list[dict], edges: dict[str, list[tuple[str, str]]] | None = None) -> tuple[list[dict], list[str]]:
    """Order selected views upstream-first using dependency edges.

    ``edges`` maps a database to (dependent, upstream) relation pairs, which may
    pass through ordinary views. Returns the ordered list and a cycle trail
    (empty when acyclic). Views without edge information keep their order.
    """
    edges = edges or {}
    ordered: list[dict] = []
    cycle: list[str] = []
    for database, items in _group(views).items():
        selected = {(item['schema'], item['name']): item for item in items}
        upstream_of: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for dependent, upstream in edges.get(database, []):
            upstream_of.setdefault(dependent, []).append(upstream)
        related: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for node in selected:
            pending = list(upstream_of.get(node, []))
            seen: set[tuple[str, str]] = set()
            found: list[tuple[str, str]] = []
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                if current in selected and current != node:
                    found.append(current)
                pending.extend(upstream_of.get(current, []))
            related[node] = sorted(found)
        visiting: set = set()
        visited: set = set()

        def visit(node, trail):
            nonlocal cycle
            if node in visiting:
                index = trail.index(node) if node in trail else 0
                cycle = [f'{database}.{s}.{n}' for s, n in trail[index:] + [node]]
                return
            if node in visited or cycle:
                return
            visiting.add(node)
            for dependency in related[node]:
                visit(dependency, trail + [node])
            visiting.discard(node)
            visited.add(node)
            ordered.append(selected[node])

        for item in items:
            visit((item['schema'], item['name']), [])
    return ordered, cycle


def _result_template(view: dict) -> dict:
    return {**view, 'key': view_key(view), 'status': 'pending', 'duration_ms': None,
            'error': None, 'started_at': None, 'finished_at': None}


def _write_checkpoint(path: Path | None, results: list[dict]) -> None:
    if path is None:
        return
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as handle:
        json.dump({'written_at': _now(), 'views': results}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_checkpoint(path: Path | None) -> list[str]:
    """Keys of views a previous local attempt already refreshed."""
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [item['key'] for item in data.get('views', []) if item.get('status') == 'succeeded' and item.get('key')]


def refresh_views(views: list[dict], progress, *, completed: list[str] | None = None,
                  checkpoint: Path | None = None, engine_factory=None,
                  sql_committed: bool = True, source_run_id=None) -> list[dict]:
    """Refresh each view in its own transaction, upstream first.

    ``progress(detail)`` receives one payload before and after every view. A
    failure stops the remaining chain and raises ``ViewRefreshError`` whose
    ``results`` retain every completed checkpoint.
    """
    from sqlalchemy import text
    factory = engine_factory or flow_sql._engine
    done = set(completed or [])
    results = [_result_template(view) for view in views]
    for item in results:
        if item['key'] in done:
            item['status'] = 'succeeded'
            item['error'] = None
            item['skipped'] = 'completed_earlier'
    total = len(results)

    def emit(stage, message, current=None):
        finished = sum(1 for item in results if item['status'] == 'succeeded')
        payload = {'stage': stage, 'message': message, 'sql_committed': sql_committed,
                   'view_refresh': {'total': total, 'completed': finished, 'current': current,
                                    'views': [dict(item) for item in results]}}
        if source_run_id is not None:
            payload['view_refresh']['source_run_id'] = source_run_id
        progress(payload)

    prefix = 'SQL insertion committed → ' if sql_committed else ''
    for index, item in enumerate(results):
        if item['status'] == 'succeeded':
            continue
        item.update(status='running', started_at=_now())
        emit('view_refresh', f"{prefix}Refreshing materialized views ({index + 1} of {total}): {label(item)}", item['key'])
        engine = factory(item['database'])
        started = time.perf_counter()
        try:
            with engine.begin() as connection:
                connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'"))
                connection.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
                connection.execute(text(f'REFRESH MATERIALIZED VIEW {qualified(item)}'))
            item.update(status='succeeded', finished_at=_now(),
                        duration_ms=round((time.perf_counter() - started) * 1000))
            _write_checkpoint(checkpoint, results)
        except Exception as exc:
            error = flow_sql._database_error(exc)
            item.update(status='failed', finished_at=_now(), error=error,
                        duration_ms=round((time.perf_counter() - started) * 1000))
            for later in results[index + 1:]:
                if later['status'] == 'pending':
                    later['status'] = 'skipped'
                    later['error'] = 'Not attempted because an upstream view failed.'
            _write_checkpoint(checkpoint, results)
            message = (f"Materialized view refresh failed for {label(item)}: {error} "
                       + ('SQL insertion had already committed; use Retry view refresh to finish the remaining views.'
                          if sql_committed else ''))
            emit('view_refresh_failed', message, item['key'])
            raise ViewRefreshError(message.strip(), results) from exc
        finally:
            engine.dispose()
    emit('view_refresh_complete', f'Refreshed {total} materialized view(s) after SQL insertion.')
    if checkpoint is not None:
        try:
            checkpoint.unlink()
        except OSError:
            pass
    return results


def execute_after_sql(job: dict, progress, artifacts, timings, state, *, checkpoint: Path | None = None,
                      engine_factory=None) -> list[dict] | None:
    """Run the frozen plan after a confirmed SQL commit; None when nothing to do."""
    views = plan_views(job)
    if not views:
        return None
    retry = job.get('job_type') == RETRY_JOB_TYPE
    completed = list((job.get('view_retry') or {}).get('completed') or []) if retry else []
    if checkpoint is not None:
        completed.extend(read_checkpoint(checkpoint))
    state['view_refresh_started'] = time.perf_counter()
    started = time.perf_counter()

    def report(detail):
        progress('running', detail, artifacts, timings)

    try:
        results = refresh_views(views, report, completed=completed, checkpoint=checkpoint,
                                engine_factory=engine_factory,
                                source_run_id=(job.get('view_retry') or {}).get('source_run_id') if retry else None)
    except ViewRefreshError as exc:
        state['view_refresh_results'] = exc.results
        timings.insert(max(0, len(timings) - 1), {'phase': 'view_refresh', 'status': 'failed',
                       'duration_ms': round((time.perf_counter() - started) * 1000), 'item_count': len(views)})
        raise
    state['view_refresh_results'] = results
    timings.insert(max(0, len(timings) - 1), {'phase': 'view_refresh', 'status': 'succeeded',
                   'duration_ms': round((time.perf_counter() - started) * 1000), 'item_count': len(views)})
    return results


def precheck_before_sql(job: dict, progress, artifacts, timings, *, engine_factory=None) -> None:
    """Recheck identities and permissions before any SQL insertion starts."""
    views = plan_views(job)
    if not views:
        return
    progress('running', {'stage': 'view_refresh_precheck',
                         'message': f'Checking {len(views)} materialized view(s) before SQL insertion.'}, artifacts, timings)
    verify_views(views, engine_factory=engine_factory)


def checkpoint_path(job: dict) -> Path | None:
    folder = (job.get('paths') or {}).get('flow_folder')
    if not folder:
        return None
    return Path(folder) / 'Scripts' / 'standalone-logs' / 'view-refresh-checkpoint.json'
