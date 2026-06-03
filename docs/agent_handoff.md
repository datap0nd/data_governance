# Agent Handoff

## Current Objective
Expose latest source row counts in Sources and Lineage, and alert when a source drops from data-bearing to empty.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit before this handoff: 06ee356
- Public repo: not re-verified in this session; treat as public unless confirmed private
- Push status: pending push for the row-count and empty-source alert work

## Decisions Made
- `source_probes.row_count` remains the storage location for row counts.
- File probes now count CSV/TXT and XLSX-family data rows, excluding one header row.
- PostgreSQL row counts continue to come from the existing probe query.
- Row count display is informational, but a transition from previous non-null row count greater than 1 to current 0 rows creates a critical `empty_source` action and alert.
- Repeated 0-row probes do not create duplicates, and later positive row counts auto-resolve open `empty_source` actions/alerts.
- Existing stale-source dedupe and stale auto-resolution were narrowed so they do not accidentally resolve `empty_source` alerts.
- Non-PostgreSQL database sources still show unknown row count unless direct connection support is added later.

## Files Changed
- app/scanner/prober.py: added CSV/TXT and XLSX-family row counting during file probes.
- app/scanner/prober.py: added empty-source transition alerting and recovery auto-resolution.
- app/models.py: added row-count fields to source and lineage response models.
- app/routers/sources.py: exposes latest probe row count on source list/detail.
- app/routers/lineage.py: exposes latest row counts in lineage edge and diagram payloads.
- app/routers/actions.py: added `empty_source` triage label, weight, recommendation, and source-row-count visibility filtering.
- app/static/app.js: adds Rows display in Sources table/detail and Lineage source cards/detail, plus an Empty Source badge.
- app/static/style.css: styles compact Lineage row-count facts and zero-row emphasis.
- app/static/index.html: bumped static asset versions.
- docs/metric_contracts.md: documented the Source rows metric.
- docs/agent_handoff.md: updated current handoff.

## Commands And Checks
- `node --check app/static/app.js`: passed.
- `PYTHONPYCACHEPREFIX=/private/tmp/dg_pycache /opt/homebrew/bin/python3.11 -m py_compile app/scanner/prober.py app/routers/actions.py app/models.py app/routers/sources.py app/routers/lineage.py`: passed.
- Row-count fixture check with temp CSV/XLSX files: returned `0 2 2`.
- Empty-source transition in-memory SQLite check: passed for no prior rows, `2 -> 0` alert creation, duplicate suppression on repeated zero, stale dedupe isolation, and auto-resolution when rows recover.
- `git diff --check`: passed.
- Direct SQLite shape checks for latest source and lineage row-count fields: passed.
- Not run: local FastAPI/uvicorn server, because this Mac environment lacks installed `fastapi` and `uvicorn`.

## Open Questions
- Whether to add direct row-count support for non-PostgreSQL database sources.

## Next Step
Run Probe Sources in the app so file-source row counts are recorded, then review Sources, Lineage, and Dashboard alerts for `empty_source` rows.
