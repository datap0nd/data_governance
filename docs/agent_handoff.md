# Agent Handoff

## Current Objective
Ensure new scanner refresh, probe, and Power BI sync work stops older pending/running work, and prevent Power BI import from waiting behind scan/probe DB writes.

## Repo State
- Path: repo root
- Branch: `main`
- Latest commit before current changes: `b7313b1 Retry PBI import through SQLite locks`
- Public repo: previously verified private, but pushed files must still remain generic and free of identifying details.
- Push status: current refresh-stop and sequencing fix is not committed yet.

## Decisions Made
- Event Log is for user actions, not refresh-debug breadcrumbs. Scheduler debug event writes were removed.
- The Reports Sync PBI button works because `POST /api/scanner/pbi-sync` calls `trigger_pbi_sync()`.
- Scanner full refresh and scheduled overall refresh must call `trigger_pbi_sync()` directly, without an internal HTTP self-call.
- Power BI sync now launches before scan/probe so the interactive scheduled task is created immediately.
- If a scheduled run fires while the sync desktop is not usable, Power BI sync is marked pending in app state and retried every minute until the session becomes usable.
- Refresh schedule settings writes need retry because SQLite can be briefly busy while the app scheduler/email loop is active.
- PBI sync can now finish while scan/probe still has SQLite locked. The import endpoint must wait and retry instead of returning HTTP 500.
- Blocking SQLite import retries should run in a FastAPI sync route/threadpool, not inside an async route event loop.
- Every new scanner refresh/probe/PBI sync should first stop pending/running scanner work, clear pending PBI retry state, stop PowerShell helper tasks/processes when available, and mark stale `running` rows as `stopped`.
- Full refresh must wait for Power BI sync import to reach a terminal status before scan/probe starts; otherwise the PBI POST can sit behind scanner SQLite writes.
- Accidental debug Event Log rows with `entity_type = 'scheduler'` should be removed on startup.

## Files Changed
- `app/scanner/control.py`: adds shared cancellation generation and stale DB run cleanup.
- `app/scanner/pbi_sync.py`: stop action now clears scanner/PBI state, PBI launch can cancel older work, full refresh can wait for import completion, and cancelled imports return `stopped`.
- `app/scanner/runner.py`: scan work accepts a cancellation generation, checks it during long loops, and records cancelled scans as `stopped`.
- `app/scanner/prober.py`: probe work accepts a cancellation generation and stops cooperatively.
- `app/routers/scanner.py`: scanner refresh now stops older work, waits for PBI import completion, then runs scan/probe without duplicate probing.
- `app/main.py`: scheduled/manual refresh-now paths stop older work, remove queued manual refresh jobs, wait for PBI import completion, and avoid overlapping daily refresh instances.
- `app/static/app.js`: Stop button wording and scanner/probe toasts handle stopped/PBI-not-completed responses.
- `tools/pbi_refresh_sync.ps1`: displays API `stopped` responses cleanly.
- `docs/agent_handoff.md`: updated current repo context.

## Commands And Checks
- Bundled Python `-m py_compile app/scanner/control.py app/scanner/pbi_sync.py app/scanner/runner.py app/scanner/prober.py app/routers/scanner.py app/main.py`: passed.
- Node `--check app/static/app.js`: passed.
- `git diff --check` on changed files: passed.
- Privacy scan on changed files for credentials, local paths, host/IP examples, and internal identifiers: passed.
- Stop/cancellation/wait behavior check with temp database: passed.
- Temp database exclusive-lock PBI import check: passed.

## Open Questions
- On the Windows host, confirm a new refresh first marks old running scan rows as stopped, then PBI sync reaches completed before scan/probe starts.

## Next Step
Commit, push, pull/update on the Windows host, restart the app service, click Stop Refresh Work once, then run a near-future scheduled refresh and confirm the PowerShell window proceeds past the report-entry count into API completion.
