# Agent Handoff

## Current Objective
Ensure scheduled refreshes trigger or retry Power BI sync when the desktop is not interactive, and stop intermittent refresh schedule save failures.

## Repo State
- Path: repo root
- Branch: `main`
- Latest commit before current changes: `4a3a176 Launch PBI sync directly before full refresh`
- Public repo: previously verified private, but pushed files must still remain generic and free of identifying details.
- Push status: current scheduled retry and schedule-save fix is not committed yet.

## Decisions Made
- Event Log is for user actions, not refresh-debug breadcrumbs. Scheduler debug event writes were removed.
- The Reports Sync PBI button works because `POST /api/scanner/pbi-sync` calls `trigger_pbi_sync()`.
- Scanner full refresh and scheduled overall refresh must call `trigger_pbi_sync()` directly, without an internal HTTP self-call.
- Power BI sync now launches before scan/probe so the interactive scheduled task is created immediately.
- If a scheduled run fires while the sync desktop is not usable, Power BI sync is marked pending in app state and retried every minute until the session becomes usable.
- Refresh schedule settings writes need retry because SQLite can be briefly busy while the app scheduler/email loop is active.
- Accidental debug Event Log rows with `entity_type = 'scheduler'` should be removed on startup.

## Files Changed
- `app/settings.py`: adds retry and longer busy timeout for app settings reads/writes.
- `app/scanner/pbi_sync.py`: adds pending Power BI sync state, scheduled defer behavior, and retry behavior.
- `app/routers/scanner.py`: full scanner run now calls `trigger_pbi_sync()` before scan/probe and returns the `pbi_sync` result.
- `app/main.py`: scheduled overall refresh now calls `trigger_pbi_sync_or_defer()` before scan/probe, retries pending PBI sync once per minute, and hardens refresh schedule saving.
- `app/static/app.js`: Scanner Power BI Sync panel shows pending sync state; refresh schedule save toast handles reschedule-warning payloads.
- `app/database.py`: removes accidental scheduler diagnostic Event Log rows.
- `docs/agent_handoff.md`: updated current repo context.

## Commands And Checks
- Bundled Python 3.12 `-m py_compile app/config.py app/database.py app/main.py app/scanner/pbi_sync.py app/routers/scanner.py app/settings.py`: passed.
- Node `--check app/static/app.js`: passed.
- Pending PBI sync defer/retry unit checks: passed.
- Refresh schedule settings save/read check with a temp database: passed.

## Open Questions
- On the Windows host, confirm a scheduled refresh while RDP is disconnected creates a pending PBI sync if it cannot launch immediately, then launches the PBI sync window after reconnect or after the guard repairs the session.

## Next Step
Commit, push, pull/update on the Windows host, restart the app service, set a near-future refresh schedule, disconnect RDP, reconnect after the scheduled time, and verify Scanner shows either a launched/completed PBI sync or a pending retry that launches within one minute of reconnect.
