# Agent Handoff

## Current Objective
Ensure every full/automated refresh triggers Power BI sync through the same launch function as the working Reports page Sync PBI button.

## Repo State
- Path: repo root
- Branch: `main`
- Latest commit before current changes: `998875e Trigger PBI sync from refresh paths via API`
- Public repo: previously verified private, but pushed files must still remain generic and free of identifying details.
- Push status: current follow-up fix is not committed yet.

## Decisions Made
- Event Log is for user actions, not refresh-debug breadcrumbs. Scheduler debug event writes were removed.
- The Reports Sync PBI button works because `POST /api/scanner/pbi-sync` calls `trigger_pbi_sync()`.
- Scanner full refresh and scheduled overall refresh must call `trigger_pbi_sync()` directly, without an internal HTTP self-call.
- Power BI sync now launches before scan/probe so the interactive scheduled task is created immediately.
- Accidental debug Event Log rows with `entity_type = 'scheduler'` should be removed on startup.

## Files Changed
- `app/config.py`: removes the abandoned internal API port setting.
- `app/scanner/pbi_sync.py`: removes the abandoned internal HTTP self-call helper.
- `app/routers/scanner.py`: full scanner run now calls `trigger_pbi_sync()` before scan/probe and returns the `pbi_sync` result.
- `app/main.py`: scheduled overall refresh now calls `trigger_pbi_sync()` before scan/probe and no longer writes scheduler diagnostics to Event Log.
- `app/static/app.js`: Scanner full refresh toast reports the PBI sync launch status; System refresh toast says PBI sync launches first.
- `app/database.py`: removes accidental scheduler diagnostic Event Log rows.
- `docs/agent_handoff.md`: updated current repo context.

## Commands And Checks
- Bundled Python 3.12 `-m py_compile app/config.py app/database.py app/main.py app/scanner/pbi_sync.py app/routers/scanner.py`: passed.
- Node `--check app/static/app.js`: passed.
- Temp database migration check: confirmed `entity_type = 'scheduler'` Event Log rows are removed and normal user-action rows remain.
- Shimmed scanner route test: confirmed `/api/scanner/run` calls the same `trigger_pbi_sync()` function as `/api/scanner/pbi-sync`, and calls it before scan/probe.
- Static scheduler assertion: confirmed `_scheduled_overall_refresh()` launches PBI sync before scan/probe.

## Open Questions
- On the Windows host, confirm Scanner > Run Scan Now and System > Refresh Schedule > Run once now both open the same PowerShell PBI sync flow as Reports > Sync PBI.

## Next Step
Commit, push, pull/update on the Windows host, restart the app service, run both refresh paths, and verify the PowerShell PBI sync window launches immediately just like Reports > Sync PBI.
