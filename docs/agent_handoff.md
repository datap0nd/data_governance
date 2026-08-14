# Agent Handoff

## Current Objective

Live-verify the restored ASAP UI scraper and the repaired Stop action on the BI
desktop after explicit approval to update the installed app.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Runtime commit: `cba6e34 Restore stable ASAP scraper and flow stop controls`
- Public repo: no, private
- Push status: runtime commit verified on `origin/main`
- Stable baseline: tag `asap-ui-automation-stable-2026-08-14` at `d2b61f1`
- Preserve untracked `governance.db-shm` and `governance.db-wal`; never stage them

## Decisions Made

- The unverified MicroStrategy REST rewrite was reverted. Runtime behavior is
  again the UI scraper from the stable `d2b61f1` baseline.
- Every scraper option selection uses a normal left click. No Playwright click
  in `app/flow_worker.py` uses a Control modifier.
- Every queued, claimed, or running flow renders Stop, regardless of headed or
  headless browser mode.
- Stopping a queued run cancels only that run. It does not terminate a worker
  currently executing a different flow.
- Stopping an assigned run terminates the exact registered worker process tree
  and leaves the run terminally cancelled. Late progress cannot resurrect it.
- The JavaScript cache key is `app.js?v=51` so the repaired Stop UI loads after
  an app update.

## Files Changed

- `app/flow_worker.py`: removed all Control-modified scraper clicks.
- `app/flow_local_runner.py`: stop the exact headed or headless worker process.
- `app/routers/flows.py`: cancel queued work safely and stop assigned work for
  either browser mode.
- `app/static/app.js`, `app/static/index.html`: show Stop for every active run,
  use the server result message, and advance the asset cache key.
- `tests/test_flows.py`: regressions for plain clicks, two queued flows, headed
  and headless stops, exact PID targeting, and Stop rendering.
- `app/asap_api.py`, `tests/test_asap_api.py`: removed with the REST rollback.

## Commands And Checks

- Full Python suite: `187 passed`.
- `node --check app/static/app.js`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- Python compile checks for the changed runtime modules: passed.
- `git diff --check`: passed.
- Remote verification: `origin/main` resolved to runtime commit `cba6e34`.
- Not run: live BI-desktop update and UI/API execution. Citrix rules require
  fresh exact approval immediately before the persistent Update App action.

## Open Questions

- Live target evidence is still required for two flows queued together: both
  rows must show Stop, cancelling the queued second flow must leave the first
  running, and stopping the active flow must close its assigned browser.
- Live week and Dimension selection must confirm plain left clicks select the
  exact requested values before RUN.

## Next Step

After explicit approval, update the installed BI-desktop app from `main`, then
run the two-flow Stop scenario and one plain-click selection flow end to end.
