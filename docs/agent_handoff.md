# Agent Handoff

## Current Objective
Make the scheduled Power BI sync complete reliably without an interactive desktop. The interactive account-picker flow (PowerShell window + auto-clicker + RDP console guard) failed whenever the PC was locked or the picker click missed.

## Repo State
- Path: repo root
- Branch: `main`
- Latest commit before current changes: `003d689 Stop stale refresh work before new scans`
- Pushed files must remain generic and free of identifying details.

## Decisions Made
- Root cause: Connect-PowerBIServiceAccount never persists tokens between processes, so every sync needed a live account-picker interaction; that can never be reliable on a locked desktop.
- New auth mode "saved Microsoft account": one-time OAuth2 device-code sign-in (code can be entered from any device), refresh token cached in `pbi_token.json` (DPAPI-encrypted on Windows, owner-only plain JSON elsewhere). All later syncs acquire tokens silently and fetch Power BI REST data in-process (`app/scanner/pbi_fetch.py`), with no PowerShell window or desktop session.
- Default public client id is the first-party Azure CLI app (pre-consented in nearly all tenants, no app registration needed); override with `DG_PBI_PUBLIC_CLIENT_ID`.
- Auth mode order in `trigger_pbi_sync()` / `trigger_pbi_usage_sync()`: service principal (PS1 background) > saved account (headless in-process thread) > interactive scheduled task (legacy fallback, unchanged).
- Headless runs reuse the existing run-status machinery: a `launched` row, then `completed` / `failed` / `stopped` rows, so `wait_for_pbi_sync_completion` and the overall-refresh gating work unchanged.
- When the refresh token is rejected (revocation, CA sign-in frequency), the sync records a failed run with reconnect guidance and inserts a critical dashboard alert (`Power BI sign-in expired...`); nothing pops or hangs. Reconnect via Scanner page.
- `_should_defer_interactive_sync` and `retry_pending_pbi_sync` skip the desktop wait / RDP guard whenever a headless mode (service principal or saved account) is available.
- Usage import logic moved from the router into `import_pbi_usage_data()` in `pbi_sync.py` (now with the same SQLite lock retry as the refresh import); the router endpoint delegates to it.
- The usage activity-events fetch still requires the Power BI/Fabric admin role, same as `Get-PowerBIActivityEvent`.

## Files Changed
- `app/scanner/pbi_auth.py` (new): device-code flow, DPAPI token cache, silent refresh, auth status.
- `app/scanner/pbi_fetch.py` (new): headless REST fetch for refresh metadata and usage activity events.
- `app/scanner/pbi_sync.py`: auth mode selection, `_launch_cached_account_sync` background thread, reconnect alert, `import_pbi_usage_data`, retry/defer logic skips desktop checks in headless modes.
- `app/routers/scanner.py`: `/api/scanner/pbi-auth/status|connect|disconnect`, status payload includes `auth` + real `auth_mode`, usage import delegates to pbi_sync.
- `app/config.py`: `DG_PBI_PUBLIC_CLIENT_ID`, `DG_PBI_AUTH_TENANT`, `DG_PBI_TOKEN_CACHE`, `DG_PBI_USAGE_DAYS_BACK`.
- `app/static/app.js`: Scanner page Power BI Connection UI (Connect/Disconnect, device-code box, reconnect badge), RDP-guard warnings only shown in interactive mode, mode-aware toasts.
- `README.md`, `docs/agent_handoff.md`: documentation.

## Commands And Checks
- `python3 -m py_compile` on all changed Python files: passed.
- `node --check app/static/app.js`: passed.
- Smoke test (mocked token endpoint + REST): cache round-trip, silent refresh with rotation, invalid_grant -> reconnect_required + persisted error, device flow to completion, disconnect, refresh payload shape parity with the PS1 script: 26/26 passed.

## Open Questions
- Confirm on the Windows host that the tenant allows the device code flow for the Azure CLI client id; if blocked, set `DG_PBI_PUBLIC_CLIENT_ID` and retry.
- If the org enforces a conditional-access sign-in frequency, the reconnect alert cadence will reveal it; reconnect is a one-click device-code redo.

## Next Step
Pull/update on the Windows host, restart the service, open Scanner, click Connect Power BI, complete the code sign-in from any device, then click Sync PBI once to verify a headless completed run. The next scheduled overall refresh should complete with the PC locked.
