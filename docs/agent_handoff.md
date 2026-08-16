# Agent Handoff

## Current Objective

Integrate SQL-loading Flows into Pipelines, add governed refresh controls for
Power BI semantic models, Flows, and PostgreSQL materialized views, and remove
redundant operational alerts. FOTA execution remains paused. Do not scan,
configure, or run FOTA until the user explicitly resumes it.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Base commit before this task: `8ab506c` (`Reorganize pipeline navigation`)
- Public repo: no, private
- Push status: implementation is locally verified and pending commit/push at
  the time of this handoff update
- Preserve untracked `governance.db-shm` and `governance.db-wal`
- Citrix was inspected read-only. No production refresh, Flow run, deployment,
  alert mutation, or other persistent Citrix change was made.

## Decisions Made

- A Flow joins a report pipeline when its enabled SQL handoff target matches
  any reachable report source by schema and table, case-insensitively.
- `flows.last_success_at` records only a successful terminal Flow run. Failed,
  cancelled, queued, and running attempts do not overwrite the last confirmed
  load timestamp.
- Flow failures are scanner-managed `flow_failed` operational actions. Target
  table freshness remains governed by the existing source probe. No Flow-age
  SLA was invented because none was specified.
- Power BI refresh means an asynchronous semantic-model refresh request through
  the Power BI REST API. The UI prevents duplicate submissions and exposes
  permission or API failures.
- Materialized-view refresh reuses the validated PostgreSQL refresh endpoint and
  tells users that freshness evidence updates on the next source probe.
- Best-practice findings remain in their dedicated checker, but no longer create
  Alerts actions. Legacy best-practice actions are resolved and hidden.
- Freshness aliases collapse to one active issue per source. Query-change alerts
  keep only the latest unresolved change per source; older changes remain in
  resolved history.

## Files Changed

- `app/routers/lineage.py`, `app/static/app.js`, `app/static/style.css`: Flow
  target matching, pipeline nodes/edges, last-load evidence, and refresh controls.
- `app/scanner/pbi_fetch.py`, `app/routers/reports.py`: Power BI semantic-model
  refresh request and audit logging.
- `app/routers/flows.py`, `app/scanner/findings.py`, `app/models.py`,
  `app/database.py`: successful-load timestamp and Flow failure alert lifecycle.
- `app/routers/actions.py`, `app/scanner/prober.py`, `app/scanner/runner.py`,
  `app/routers/best_practices.py`: best-practice removal and duplicate prevention.
- `tests/`: Flow lineage, last-success/failure lifecycle, Power BI request,
  alert cleanup, and Pipelines control coverage.

## Commands And Checks

- `PYTHONPATH=. uv run --python 3.13 --with pytest --with-requirements
  requirements.txt pytest -q tests --ignore=tests/test_lineage_depth.py`: 267 passed.
- Isolated `tests/test_lineage_depth.py`: 5 passed. It is isolated because its
  minimal FastAPI stub pollutes later test collection when combined.
- `node tests/test_lineage_layers.mjs`: passed.
- `python3 -m compileall -q app`, `node --check app/static/app.js`, and
  `git diff --check`: passed.
- Rendered local Pipelines preview against a temporary database: report, Flow,
  and materialized-view controls visible; Flow appears upstream of its target;
  last successful load visible; browser console had no errors.
- Live Citrix dashboard read-only audit: 124 active alerts. Repeated freshness
  aliases and superseded query-change rows explained much of the redundancy;
  best-practice actions were also present.
- Not run: live Power BI refresh, live Flow refresh, live materialized-view
  refresh, production source mapping, deployment, or post-deployment Citrix
  verification. The production Flow target does not exist yet and refreshes
  would mutate live systems.

## Open Questions

- Production acceptance still requires a future scan after the target tables
  exist, plus authorized live refresh tests for each supported node type.
- The Citrix app remains on an older build until the normal Update App deployment
  is explicitly performed.
- FOTA remains paused independently of the Pipelines feature work.

## Next Step

Commit and push these scoped changes to `origin/main`. Later, after the SQL target
tables exist and live mutation is authorized, deploy the exact tested commit,
refresh report metadata, and verify the complete Pipelines refresh path in Citrix.
