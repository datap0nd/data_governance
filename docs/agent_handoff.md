# Agent Handoff

## Current Objective
Remove the application admin/non-admin split so every registered user has the same app access.

## Repo State
- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest functional commit: `b04400a Remove app admin access split`
- Public repo: verified private with `gh repo view datap0nd/data_governance --json visibility,nameWithOwner`
- Push status: functional commit pushed to `origin/main`; this handoff update is a follow-up repo-context commit.
- Untracked local artifacts remain intentionally unstaged: `.DS_Store`, `PRODUCT.md`, screenshot files, `mockup_lineage.html`, `package*.json`, `screenshots/`, and `ui_review*.mjs` / `ui_verify.mjs`.

## Decisions Made
- Removed IP allowlist based app access. All requests now pass the generic app-access hook.
- Kept the `is_admin: true` response field for compatibility with existing clients that may still read it.
- Removed the Admin Access UI and `/api/admin/access` toggle endpoint.
- Renamed the user-facing Admin menu to System.
- Moved refresh schedule frontend calls to `/api/system/...`.
- Kept hidden `/api/admin/refresh-*` aliases for backward compatibility only.
- Stopped creating the old `admin_user_ips` table for new databases. Existing databases may retain the unused table harmlessly.

## Files Changed
- `app/local_access.py`: removed admin/IP allowlist enforcement and replaced it with generic app-access helpers.
- `app/main.py`: sets every request/user response as admin-compatible, removes access toggle endpoints, adds System refresh routes.
- `app/database.py` and `app/config.py`: remove the admin allowlist table setup and `DG_ADMIN_EVERYONE` flag.
- `app/static/index.html`, `app/static/app.js`, `app/static/style.css`: show formerly hidden controls, remove Admin Access page/routing/styles, rename nav to System, remove user admin badge.
- `app/routers/scanner.py`, `app/routers/scripts.py`, `app/routers/scheduled_tasks.py`, `app/routers/usage.py`: rename old local/admin access hooks to generic access hooks.
- `README.md`, `docs/metric_contracts.md`, `app/routers/changelog.py`: update stale admin wording.

## Commands And Checks
- `node --check app/static/app.js`: passed.
- `python3 -m compileall app`: passed.
- Bundled Python 3.12 `-m compileall app`: passed.
- Temp Python 3.12 venv with `api/requirements.txt`: `import app.main` passed.
- Smoke server on `127.0.0.1:8765` with `DG_DB_PATH=/tmp/data_governance_smoke.db`: started and stopped cleanly.
- `curl /api/me`: returned `is_admin: true`.
- `curl /api/system/refresh-schedule`: returned schedule payload.
- `curl /api/admin/access`: returned `404`, confirming the removed toggle endpoint.
- Playwright using local Google Chrome: nav renders System, no Admin Access link, System links visible, no console errors.
- `git diff --check` and `git diff --cached --check`: passed.

## Open Questions
- None blocking. If old external tooling called `/api/admin/access`, it will need to stop using that removed toggle endpoint.

## Next Step
On the target machine, update the app and confirm a remote registered user can see System > Refresh Schedule, Premium Viewers, scanner actions, and update controls without any app-level access toggle.
