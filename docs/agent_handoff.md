# Agent Handoff

## Current Objective

Deliver and live-verify a PostgreSQL managed snapshot flow that creates its
target on the first run and refreshes it safely on later full-history runs.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest runtime commit: `3ba9c78 Implement managed SQL snapshots`
- Latest commit before this handoff update: `3ba9c78 Implement managed SQL snapshots`
- Public repo: no, private
- Push status: runtime commit verified pushed to `origin/main`
- Preserve untracked `governance.db-shm` and `governance.db-wal`; never stage them

## Decisions Made

- Internal SQL mode `replace` now means managed snapshot refresh for backward
  compatibility with saved flows. It no longer drops the target table.
- A missing target is created from a transaction-scoped staging table whose
  normalized CSV columns are nullable PostgreSQL `TEXT`.
- An existing target keeps its object identity, grants, indexes, constraints,
  triggers, and older nullable columns. New CSV columns are added as nullable
  `TEXT`; prior rows are truncated and the staged full snapshot is inserted.
- Staging, schema evolution, row replacement, and commit share one transaction.
  Any COPY, constraint, or promotion failure rolls everything back.
- Required existing columns absent from the CSV fail before the target changes.
- Append still requires an existing table and maps normalized CSV headers to
  exact target column names.
- Managed snapshot may use a new table name inside a schema from the latest SQL
  catalog scan. The SQL account needs schema `USAGE` and `CREATE`; refreshing an
  existing table also requires ownership.

## Files Changed

- `app/flow_sql.py`: implement transactional managed snapshot creation,
  refresh, schema evolution, rollback, and 63-byte identifier validation.
- `app/routers/flows.py`: allow a new managed table name in a discovered schema.
- `app/static/app.js`: expose managed snapshot behavior and editable table name.
- `app/static/index.html`: invalidate the main JavaScript cache.
- `app/static/flow_run_log.js`: correct SQL-only retry consequences.
- `app/static/flow_run_log.html`: invalidate the run-log JavaScript cache.
- `README.md`: document current SQL handoff behavior and remove a stale limit.
- `tests/test_flow_sql.py`: cover create, refresh, schema evolution, and rollback.
- `tests/test_flows.py`: cover API validation and both UI surfaces.

## Commands And Checks

- Full Python suite: `207 passed in 2.79s`.
- Real temporary PostgreSQL 16 integration: passed create-on-first-run, refresh,
  stable table OID, nullable schema evolution, constraint/index preservation,
  rollback after a forced unique violation, staging cleanup, and exact-column
  append compatibility.
- `node --check app/static/app.js`: passed.
- `node --check app/static/flow_run_log.js`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- `python3.11 -m py_compile app/flow_sql.py app/flow_worker.py app/routers/flows.py`: passed.
- `git diff --check`: passed.
- Citrix and the live PostgreSQL target were not mutated or tested in this task.

## Open Questions

- The managed snapshot implementation has not been installed or exercised
  through the live Metronome-to-PostgreSQL path.
- The configured SQL role still needs persistent `USAGE` and `CREATE` on the
  selected schema before a first-run target can be created there.
- Prior week-selection and two-queued-flow Stop changes still require their
  separate live acceptance checks.

## Next Step

After exact approval, grant the configured SQL role `USAGE` and `CREATE` on the
chosen schema, install the exact delivered main commit, configure a new managed
target name, and run one full snapshot twice to verify create then refresh.
