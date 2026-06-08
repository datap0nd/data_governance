# Agent Handoff

## Current Objective
Fix Power BI sync hanging after RDP leaves the BI machine locked or disconnected, without relying on tenant access, service principal setup, direct headless API access, or users remembering to run a manual command.

## Repo State
- Path: `data_governance`
- Branch: `main`
- Latest commit: `c3be922`
- Public repo: not re-verified in this session; treat as private unless confirmed otherwise
- Push status: local changes are not committed or pushed
- Important dirty-state note: this work was done on top of existing uncommitted dashboard/admin UI changes. In particular, most of the current `app/static/app.js` diff predates the RDP sync fix.

## Decisions Made
- A locked or disconnected Windows desktop cannot reliably drive the Microsoft account picker. With no tenant/service-principal path available, the practical solution is to keep the sync account's interactive desktop recoverable.
- The earlier manual `tscon` helper is not sufficient because multiple users RDP to the machine and will not remember to run it.
- Added an automatic RDP console guard:
  - periodic scheduled task every five minutes
  - event-triggered scheduled task on RDP disconnect event ID 24
  - pre-sync guard call immediately before refresh and usage sync launch
- The guard targets `DG_PBI_SYNC_WINDOWS_USER` and only repairs disconnected sessions by default. It does not kick actively connected RDP users.
- If someone is actively using a different account during the sync window, that still needs an operating rule such as reserving the sync window or using a dedicated sync account.
- The watchdog and preflight from the previous pass remain: sync scripts fail fast and record clear failure messages instead of hanging overnight.
- After field feedback that overall refresh worked but PBI sync did not, tightened the pre-sync guard: the app now verifies with `quser` that the sync user is actually active on the console before launching PBI sync. A launched guard task is no longer treated as proof of readiness.
- `/api/scanner/pbi-sync/status` now includes `rdp_guard`.

## Files Changed
- `tools/rdp_console_guard.ps1`: transfers the configured sync user's disconnected RDP session back to console with `tscon`.
- `tools/install_rdp_console_guard.ps1`: installs the periodic SYSTEM task and the Terminal Services disconnect event task.
- `setup.ps1`: sets `DG_PBI_SYNC_WINDOWS_USER` and installs the guard during setup/update without blocking app startup if guard installation fails.
- `app/config.py`: reads `DG_PBI_SYNC_WINDOWS_USER`.
- `app/scanner/pbi_sync.py`: runs the guard before refresh and usage sync task launch.
- `app/routers/scanner.py`: returns RDP guard diagnostics in PBI sync status.
- `tools/pbi_sync_helpers.ps1`: updated failure messages to reference the automatic guard rather than a manual RDP command.
- `tools/disconnect_rdp_keep_unlocked.ps1`: retained as a manual fallback helper, no longer the main strategy.
- `tools/pbi_refresh_sync.ps1` and `tools/pbi_usage_sync.ps1`: still include interactive preflight and watchdog timeout from the prior pass.
- `README.md`: documents the automatic guard, security tradeoff, target sync user, status diagnostics, remaining active-user limitation, and stale-email safeguards.
- `app/static/app.js`: still has uncommitted pre-existing dashboard work plus local RDP status UI experiments; do not assume those frontend changes were pushed unless committed separately.

## Commands And Checks
- `python3 -m py_compile app/config.py app/scanner/pbi_sync.py`: passed.
- `python3 -m py_compile app/config.py app/scanner/pbi_sync.py app/routers/scanner.py`: passed after adding guard diagnostics.
- `node --check app/static/app.js`: passed.
- `git diff --check -- README.md app/config.py app/scanner/pbi_sync.py app/static/app.js docs/agent_handoff.md setup.ps1 tools/pbi_refresh_sync.ps1 tools/pbi_usage_sync.ps1 tools/pbi_sync_helpers.ps1 tools/disconnect_rdp_keep_unlocked.ps1 tools/rdp_console_guard.ps1 tools/install_rdp_console_guard.ps1`: passed.
- Basic brace-count sanity check over touched PowerShell files: passed.
- Targeted scan for PATs, local macOS paths, and em dashes in touched files: passed.
- Isolated `quser` parser sample test for active RDP, disconnected, and console rows: passed.
- Not run: PowerShell parser/runtime validation because `pwsh`/`powershell` is not installed on this macOS environment.
- Not run: live RDP/Power BI validation because it must be tested on the target Windows BI machine.

## Open Questions
- Confirm on the Windows BI machine that `tools\install_rdp_console_guard.ps1 -TargetUser <sync-user>` creates both tasks successfully.
- Confirm the Terminal Services event log is enabled and event ID 24 fires on RDP disconnect.
- Confirm `quser` state values match the guard parser and that `tscon <session-id> /dest:console` works from the SYSTEM task.
- Decide whether to reserve the sync window or allow an explicit active-session transfer mode if users are often actively connected during the sync time.

## Next Step
Deploy/update on the Windows BI machine, confirm the `DG_RDP_Console_Guard` and `DG_RDP_Console_Guard_OnDisconnect` scheduled tasks exist, disconnect an RDP session for the sync user, wait up to five minutes, then trigger `Sync PBI` and confirm it completes or records a clear failed-session message instead of hanging.
