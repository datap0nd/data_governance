"""Server-side discovery and verification for post-SQL materialized-view refresh.

Automatic mode walks the PostgreSQL dependency metadata the Pipelines feature
already maintains (``source_postgres_identities`` and ``source_dependencies``)
from the Flow's exact SQL table through every downstream relation, including
ordinary views, and emits only physical materialized views, upstream first.
Manual mode verifies an explicit selection. Nothing here writes to PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import flow_view_refresh as executor
from app.source_identity import normalize_server, postgres_identity_tuple

METADATA_MAX_AGE = timedelta(hours=48)
KIND_LABELS = {'materialized_view': 'materialized view', 'view': 'view', 'table': 'table', 'foreign_table': 'foreign table'}


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00').replace(' ', 'T'))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _now():
    return datetime.now(timezone.utc)


def _identity_rows(db, server: str):
    return db.execute(
        """SELECT spi.source_id, spi.server_name, spi.database_name, spi.schema_name,
                  spi.relation_name, spi.relation_kind, spi.verified_at, s.name AS source_name
           FROM source_postgres_identities spi JOIN sources s ON s.id=spi.source_id
           WHERE spi.server_name=? AND COALESCE(s.archived,0)=0""",
        (normalize_server(server),),
    ).fetchall()


def _view_from_row(row) -> dict:
    return {'database': row['database_name'], 'schema': row['schema_name'], 'name': row['relation_name']}


def catalog_edges(db, server: str, databases: set[str]) -> dict[str, list[tuple[tuple[str, str], tuple[str, str]]]]:
    """(dependent, upstream) relation pairs per database from the scanned catalog."""
    identities = {row['source_id']: row for row in _identity_rows(db, server)}
    edges: dict[str, list] = {}
    for dependent_id, upstream_id in db.execute(
        'SELECT source_id, depends_on_id FROM source_dependencies'
    ).fetchall():
        dependent, upstream = identities.get(dependent_id), identities.get(upstream_id)
        if not dependent or not upstream:
            continue
        if dependent['database_name'] != upstream['database_name'] or dependent['database_name'] not in databases:
            continue
        edges.setdefault(dependent['database_name'], []).append(
            ((dependent['schema_name'], dependent['relation_name']), (upstream['schema_name'], upstream['relation_name'])))
    return edges


def catalog_materialized_views(db, server: str, query: str = '', *, limit: int = 200) -> list[dict]:
    """Searchable list of known materialized views on the configured server."""
    needle = (query or '').strip().casefold()
    result = []
    for row in _identity_rows(db, server):
        if row['relation_kind'] != 'materialized_view':
            continue
        view = _view_from_row(row)
        text = executor.label(view).casefold()
        if needle and needle not in text and needle not in (row['source_name'] or '').casefold():
            continue
        result.append({**view, 'key': executor.view_key(view), 'source_id': row['source_id'],
                       'source_name': row['source_name'], 'verified_at': row['verified_at']})
    result.sort(key=lambda item: (item['database'], item['schema'], item['name']))
    return result[:limit]


def discover_automatic(db, server: str, target: dict) -> dict:
    """Walk downstream from the exact SQL table and return the refresh setup.

    ``status`` is one of ``ok`` (a verified, possibly empty list), ``missing``
    (the table is not in the metadata), ``ambiguous``, ``incomplete`` (a
    downstream relation lacks an identity), ``cyclic`` or ``unconfigured``.
    ``stale`` is reported separately as a warning with the metadata timestamp.
    """
    database, schema, table = (target.get('database') or '').strip(), (target.get('schema') or '').strip(), (target.get('table') or '').strip()
    base = {'mode': 'automatic', 'views': [], 'blockers': [], 'warnings': [], 'metadata_at': None,
            'discovered_at': _now().isoformat(), 'stale': False, 'target': {'database': database, 'schema': schema, 'table': table}}
    if not (server and database and schema and table):
        return {**base, 'status': 'unconfigured',
                'blockers': ['Choose the SQL database, schema and table first; automatic discovery starts from that exact table.']}
    identities = {row['source_id']: row for row in _identity_rows(db, server)}
    coordinates = postgres_identity_tuple(server=server, database=database, schema=schema, relation=table)
    roots = [row for row in identities.values()
             if (row['server_name'], row['database_name'], row['schema_name'], row['relation_name']) == coordinates]
    if not roots:
        return {**base, 'status': 'missing',
                'blockers': [f'{database}.{schema}.{table} is not in the PostgreSQL dependency metadata yet. '
                             'Refresh the metadata (Scanner → PostgreSQL lineage) or choose Manual.']}
    if len(roots) > 1:
        return {**base, 'status': 'ambiguous',
                'blockers': [f'{database}.{schema}.{table} matches more than one catalog source; resolve the duplicate '
                             'identities in Lineage or choose Manual.']}
    root = roots[0]
    dependents: dict[int, list[int]] = {}
    for dependent_id, upstream_id in db.execute('SELECT source_id, depends_on_id FROM source_dependencies').fetchall():
        dependents.setdefault(upstream_id, []).append(dependent_id)
    blockers: list[str] = []
    warnings: list[str] = []
    found: dict[str, dict] = {}
    involved = [root]
    cycle: list[str] = []
    order: list[int] = []
    visiting: set[int] = set()
    done: set[int] = set()

    def visit(source_id: int, trail: list[int]):
        nonlocal cycle
        if source_id in visiting:
            if not cycle:
                index = trail.index(source_id) if source_id in trail else 0
                cycle = [_label(identities.get(item), item) for item in trail[index:] + [source_id]]
            return
        if source_id in done or cycle:
            return
        visiting.add(source_id)
        for child_id in sorted(dependents.get(source_id, [])):
            child = identities.get(child_id)
            if child is None:
                name = db.execute('SELECT name FROM sources WHERE id=?', (child_id,)).fetchone()
                blockers.append(f"Downstream relation {name['name'] if name else '#' + str(child_id)} has no verified PostgreSQL "
                                'identity; refresh the metadata or choose Manual.')
                continue
            if child['server_name'] != normalize_server(server):
                warnings.append(f"{_label(child, child_id)} is on {child['server_name']}, not the configured SQL server, and is skipped.")
                continue
            involved.append(child)
            visit(child_id, trail + [source_id])
        visiting.discard(source_id)
        done.add(source_id)
        order.append(source_id)

    visit(root['source_id'], [])
    if cycle:
        return {**base, 'status': 'cyclic', 'metadata_at': root['verified_at'],
                'blockers': ['Materialized-view dependency cycle: ' + ' → '.join(cycle) + '. Fix the lineage or choose Manual.']}
    if blockers:
        return {**base, 'status': 'incomplete', 'metadata_at': root['verified_at'], 'blockers': list(dict.fromkeys(blockers)), 'warnings': warnings}
    # Post-order emits upstream relations before the views that read them.
    for source_id in order:
        row = identities.get(source_id)
        if row is None or source_id == root['source_id'] or row['relation_kind'] != 'materialized_view':
            continue
        view = _view_from_row(row)
        key = executor.view_key(view)
        if key not in found:
            found[key] = {**view, 'key': key, 'source_id': source_id, 'source_name': row['source_name'], 'depth': None}
    verified_times = [_parse_time(row['verified_at']) for row in involved]
    oldest = min((item for item in verified_times if item), default=None)
    stale = oldest is None or _now() - oldest > METADATA_MAX_AGE
    if stale:
        warnings.append('The dependency metadata is older than 48 hours; refresh it to be sure the list is current.')
    # Order the deduplicated physical views upstream first using the same
    # dependency edges (which may pass through ordinary views).
    plain = [{'database': v['database'], 'schema': v['schema'], 'name': v['name']} for v in found.values()]
    ordered, cycle = executor.order_views(plain, catalog_edges(db, server, {v['database'] for v in plain}))
    if cycle:
        return {**base, 'status': 'cyclic', 'metadata_at': root['verified_at'],
                'blockers': ['Materialized-view dependency cycle: ' + ' → '.join(cycle) + '. Fix the lineage or choose Manual.']}
    views = [found[executor.view_key(v)] for v in ordered]
    return {**base, 'status': 'ok', 'views': views, 'metadata_at': root['verified_at'],
            'oldest_metadata_at': oldest.isoformat() if oldest else None, 'stale': stale,
            'warnings': list(dict.fromkeys(warnings))}


def _label(row, source_id) -> str:
    if row is None:
        return f'source #{source_id}'
    return f"{row['database_name']}.{row['schema_name']}.{row['relation_name']}"


def verify_manual(db, server: str, views: list[dict], *, engine_factory=None, inspect: bool = True) -> dict:
    """Check a manual selection against the catalog and, when possible, PostgreSQL."""
    normalized = []
    for item in views:
        view = executor.normalize_view(item)
        if view is None:
            raise ValueError('Each materialized view needs a database, schema and view name.')
        if view not in normalized:
            normalized.append(view)
    blockers: list[str] = []
    warnings: list[str] = []
    known = {executor.view_key(_view_from_row(row)): row for row in _identity_rows(db, server)}
    findings = None
    if inspect and normalized:
        try:
            findings = executor.inspect_views(normalized, engine_factory=engine_factory)
        except Exception as exc:
            from app.flow_sql import _database_error
            warnings.append('PostgreSQL could not be inspected: ' + _database_error(exc) + '. The selection is unverified.')
    if findings is not None:
        blockers.extend(executor.verification_problems(findings))
    else:
        for view in normalized:
            row = known.get(executor.view_key(view))
            if row is None:
                warnings.append(f'{executor.label(view)} is not in the catalog metadata and was not verified against PostgreSQL.')
            elif row['relation_kind'] != 'materialized_view':
                blockers.append(f"{executor.label(view)} is a {KIND_LABELS.get(row['relation_kind'], row['relation_kind'])}, not a materialized view.")
    databases = {view['database'] for view in normalized}
    edges = catalog_edges(db, server, databases)
    if inspect and normalized:
        for database in sorted(databases):
            try:
                edges.setdefault(database, []).extend(executor.inspect_dependencies(database, engine_factory=engine_factory))
            except Exception as exc:
                from app.flow_sql import _database_error
                warnings.append(f'Dependency order for {database} uses catalog metadata only: ' + _database_error(exc))
    ordered, cycle = executor.order_views(normalized, edges)
    if cycle:
        blockers.append('Materialized-view dependency cycle: ' + ' → '.join(cycle) + '.')
    items = []
    for view in ordered:
        row = known.get(executor.view_key(view))
        finding = next((item for item in findings or [] if executor.view_key(item) == executor.view_key(view)), None)
        items.append({**view, 'key': executor.view_key(view), 'source_id': row['source_id'] if row else None,
                      'source_name': row['source_name'] if row else None,
                      'verified': bool(finding and finding.get('can_refresh')),
                      'catalog': bool(row)})
    return {'mode': 'manual', 'status': 'ok' if not blockers else 'blocked', 'views': items, 'blockers': list(dict.fromkeys(blockers)),
            'warnings': list(dict.fromkeys(warnings)), 'discovered_at': _now().isoformat(),
            'metadata_at': max((row['verified_at'] for row in known.values()), default=None) if known else None}


def build_plan(db, config: dict, sql_target: dict, server: str) -> dict:
    """Freeze the refresh list for one job without contacting PostgreSQL.

    A blocked automatic discovery yields ``blocked`` text instead of raising so
    the saved Flow, its handover files and the queue path can explain it.
    """
    try:
        config = executor.normalize_config(config)
    except ValueError as exc:
        return {'mode': 'off', 'views': [], 'blocked': str(exc)}
    if config['mode'] == 'off' or not sql_target.get('enabled'):
        return {'mode': config['mode'], 'views': []}
    if config['mode'] == 'automatic':
        discovered = discover_automatic(db, server, sql_target)
        if discovered['status'] != 'ok':
            return {'mode': 'automatic', 'views': [], 'blocked': ' '.join(discovered['blockers']),
                    'status': discovered['status'], 'metadata_at': discovered.get('metadata_at')}
        views = [{'database': v['database'], 'schema': v['schema'], 'name': v['name']} for v in discovered['views']]
        return {'mode': 'automatic', 'views': views, 'discovered_at': discovered['discovered_at'],
                'metadata_at': discovered.get('metadata_at'), 'stale': discovered.get('stale', False),
                'server': normalize_server(server)}
    edges = catalog_edges(db, server, {view['database'] for view in config['views']})
    ordered, cycle = executor.order_views(config['views'], edges)
    if cycle:
        return {'mode': 'manual', 'views': [], 'blocked': 'Materialized-view dependency cycle: ' + ' → '.join(cycle) + '.'}
    return {'mode': 'manual', 'views': ordered, 'discovered_at': _now().isoformat(), 'server': normalize_server(server)}
