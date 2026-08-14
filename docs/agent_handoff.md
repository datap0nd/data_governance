# Agent Handoff

## Current Objective

Live-verify the new browser-authenticated MicroStrategy REST path for ASAP on
the BI desktop. The code migration is delivered; the BI desktop has not yet
been updated to this build.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest commit: `eb9d61d Replace ASAP UI automation with MicroStrategy REST`
- Public repo: no, private
- Push status: `origin/main` is verified at `eb9d61d`
- Stable rollback tag: `asap-ui-automation-stable-2026-08-14`, peeled to
  `d2b61f10c84a750761ff3e5a1eb239f32d4024db`
- Untracked local files: `governance.db-shm` and `governance.db-wal`; preserve
  them and never stage them

## Decisions Made

- ASAP browser automation now stops after browser SSO. The worker exchanges the
  browser session for a short-lived token through
  `GET /MicroStrategyLibrary/api/auth/token` and keeps the token in memory only.
- Weekly discovery traverses accessible projects and Shared Reports folders by
  stable MicroStrategy object IDs, creates report instances, reads prompt
  definitions, and pages prompt members in batches of 200 until exhausted.
- Catalog records persist project IDs, report IDs, prompt keys, prompt types,
  and option IDs in existing JSON fields. Existing SQLite tables remain
  migration-compatible; missing entries are marked stale, never deleted.
- A prompted report cannot be one vendor HTTP request. One Metronome download
  operation explicitly wraps instance creation, prompt answers, nested-prompt
  checks, paged Data API v2 retrieval, and instance cleanup.
- Normal Data API grids are converted to CSV from their definitions and raw
  values. Cross-tabs, column attributes, unsupported prompt types, missing IDs,
  incomplete paging, and result-shape changes fail closed.
- Full scans are atomic from the governance catalog's perspective. An API error
  fails the scan and does not apply partial discoveries or stale prior rows.
- SQL transformation and insertion code was not changed.
- The prior geometry and rendered-control helpers remain in `flow_worker.py`
  but have no ASAP discovery or execution callers. The stable tag preserves the
  former runtime if rollback is needed.

## Files Changed

- `app/asap_api.py`: browser-session token exchange, ID-based discovery, prompt
  pagination, prompt answering, result paging, and lossless normal-grid CSV.
- `app/flow_worker.py`: route ASAP scans and downloads through the REST adapter;
  browser use is limited to SSO and token creation.
- `app/routers/flows.py`: include site base URL in jobs, target report scans by
  project/report ID, reject legacy targeted scans, and accept large API catalogs.
- `app/static/app.js`: explain the MicroStrategy API catalog and SSO/token model.
- `README.md`: document the API workflow, required multi-call vendor contract,
  paging, and fail-closed limits.
- `tests/test_asap_api.py`, `tests/test_flows.py`: API token, pagination, ID
  mapping, result flattening, staging, and migration regressions.

## Commands And Checks

- `PYTHONPATH=. uv run --python 3.11 --with-requirements requirements.txt --with pytest python -m pytest -q`: `190 passed`.
- `node --check app/static/app.js`: passed.
- `python3 -m py_compile app/asap_api.py app/flow_worker.py app/routers/flows.py`: passed.
- `git diff --check`: passed.
- Citrix read-only network inspection: live ASAP returned HTTP 200 structured
  requests including `menuInfo.do`, `objects`, `getNewReport`, and related
  catalog fetches. This proved that the old geometry crawler was unnecessary.
- Official Library REST compatibility has not yet been proven live. Citrix text
  injection failed while attempting a direct `/MicroStrategyLibrary/api/status`
  navigation, so no result was inferred.

## Open Questions

- Does the deployed ASAP Library expose `GET /api/auth/token`, projects, folders,
  report instances, prompts, prompt elements, and Data API v2 to this SSO user?
- Are the production ASAP exports normal report grids? Cross-tab exports fail
  deliberately until a lossless expansion contract is added.
- Nested prompts are handled during execution, but a weekly catalog cannot
  pre-enumerate every conditional option combination without an exponential
  traversal. The current catalog stores the options the documented instance
  exposes and fails if a runtime prompt is absent from the latest catalog.

## Next Step

Obtain explicit approval to click `Update App` in Citrix because it persistently
changes the BI desktop. Then update to `eb9d61d`, open Flows > Catalog, run one
full API catalog refresh, and inspect the terminal scan status. If it succeeds,
create a download-only test flow and validate report rows before enabling any
SQL handoff. If Library REST is unavailable, keep the scan failed and capture
the exact HTTP status instead of restoring UI guessing.
