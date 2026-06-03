# Agent Handoff

## Current Objective
Make Power BI automated sync reliable when the Windows PC is locked, prevent scheduled emails from sending stale PBI metadata, and let admins change/test the overall refresh schedule.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit before this handoff: d8ea907
- Public repo: not re-verified in this session; treat as public unless confirmed private
- Push status: local changes not committed or pushed in this session

## Decisions Made
- GUI account-picker automation is not reliable on a locked Windows desktop.
- Power BI sync now supports unattended service-principal auth via generic environment variables.
- Existing interactive auth and auto-clicker remain as fallback when service-principal config is absent.
- PBI sync attempts and completions are recorded in `pbi_sync_runs`.
- Scheduled emails that depend on PBI freshness are deferred and retried when PBI refresh sync is stale or missing.
- After the configured overall refresh time plus a grace window, scheduled emails require that day's PBI refresh sync to have completed.
- Admin Access work from the previous task remains in the working tree: remote admin is granted by registered IP row, not hardcoded person name.
- Overall refresh is now one admin-configurable daily job that runs report scan, source probe, and Power BI sync together.
- Admins can queue a one-off overall refresh immediately from Admin > Refresh Schedule.

## Files Changed
- app/config.py: added PBI service-principal and email freshness/defer configuration.
- app/settings.py: added persisted app settings helpers for overall refresh time.
- app/database.py: added `admin_user_ips`, `pbi_sync_runs`, and `app_settings` schema/migrations.
- app/local_access.py: added admin-IP schema helper and shared admin permission checks.
- app/main.py: added Admin Access APIs, `is_admin` identity payload, and admin-configurable overall refresh scheduler APIs.
- app/routers/scanner.py: changed scanner gates to admin, added PBI sync status/failure recording, and records usage sync completion.
- app/routers/usage.py: changed usage admin gate to shared admin gate.
- app/scanner/pbi_sync.py: added service-principal launch mode, sync run recording, freshness helpers, and background PowerShell launch.
- app/routers/email_schedules.py: blocks/deferred scheduled email sends when PBI sync is stale, using the saved overall refresh time.
- tools/pbi_refresh_sync.ps1: added service-principal auth and failure status callback.
- tools/pbi_usage_sync.ps1: added service-principal auth and failure status callback.
- app/static/app.js: added Admin Access UI, Refresh Schedule UI, and Scanner PBI sync status panel.
- app/static/index.html: added Admin Access and Refresh Schedule nav entries and bumped static versions.
- app/static/style.css: added Admin Access, Refresh Schedule, and PBI sync status styling.
- README.md: documented Admin Access, unattended Power BI sync setup, and overall refresh scheduling.
- docs/agent_handoff.md: updated current handoff.

## Commands And Checks
- `node --check app/static/app.js`: passed.
- `PYTHONPYCACHEPREFIX=/private/tmp/dg_pycache /opt/homebrew/bin/python3.11 -m py_compile app/settings.py app/config.py app/database.py app/scanner/pbi_sync.py app/routers/scanner.py app/routers/email_schedules.py app/local_access.py app/main.py app/routers/usage.py`: passed.
- `git diff --check`: passed.
- Privacy scan over touched files: no committed personal name, local path, PAT, or real secret found; hits were placeholders and generic env names.
- Not run: PowerShell syntax validation, because `pwsh` is not installed on this Mac environment.
- Not run: local FastAPI/uvicorn server, because this Mac environment lacks installed `fastapi`.

## Open Questions
- Whether the tenant can allow a Power BI service principal to access the required workspace and activity-event APIs.
- Whether IP-based admin should later be strengthened with browser/device key binding.

## Next Step
Configure a Power BI service principal for the Windows service account, restart the app service, then set Admin > Refresh Schedule to a near-future time or use Run once now. Check Admin > Scanner for service-principal mode and fresh PBI sync status before enabling morning emails.
