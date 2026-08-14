# Agent Handoff

## Current Objective

Live-verify state-confirmed plain-click selection on the nine-download ASAP flow,
then verify Stop behavior with two queued flows.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Previous runtime commit: `5c14d6d Confirm every ASAP selection click`
- Hardened retry audit changes are pending publication with this handoff
- Public repo: no, private
- Stable baseline: tag `asap-ui-automation-stable-2026-08-14` at `d2b61f1`
- Preserve untracked `governance.db-shm` and `governance.db-wal`; never stage them

## Decisions Made

- The unverified MicroStrategy REST rewrite remains reverted. Runtime behavior
  uses the UI scraper restored from the stable baseline.
- All scraper member selections use normal left clicks without Control.
- A click is no longer considered successful until MicroStrategy exposes the
  requested rendered state. Each member is retried up to three times.
- An unselected MicroStrategy member can expose neither true nor false. Any
  requested value not confirmed true is missing and must be selected.
- Exact selection must remain stable across three reads before RUN is allowed.
- Dimension clearing also reconciles across four rounds instead of ignoring a
  failed three-click attempt. Delayed rendered confirmation is awaited before
  another click can toggle the member back off.
- Every queued, claimed, or running flow renders Stop. Cancelling a queued run
  does not terminate another flow's worker; assigned runs target their exact
  headed or headless worker process.

## Files Changed

- `app/flow_worker.py`: replace optimistic fixed-delay list clicks with
  state-confirmed plain-click reconciliation for week and Dimension prompts.
- `tests/test_flows.py`: reproduce an unknown unselected final week whose first
  click is dropped, then require a successful retry with no modifier.

## Commands And Checks

- Live target evidence: run `#83` completed eight of nine downloads, then the
  ninth configuration requested weeks `202629` through `202632` but retained
  only `202629`, `202630`, and `202631`.
- Root cause: the restored retry loop treated only explicit false as missing;
  the portal reported the unselected final member as unknown, so no retry ran.
- Full Python suite: `190 passed`.
- Selection stress simulation: `20,000` cases passed across week and Dimension
  prompts, true/false and unknown states, arbitrary retained selections, and
  zero through five dropped clicks per member.
- `node --check app/static/app.js`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- `python3 -m py_compile app/flow_worker.py`: passed.
- `git diff --check`: passed.
- Citrix inspection was read-only. Nothing was edited, saved, refreshed,
  applied, published, exported, sent, or deleted in the remote session.

## Open Questions

- The new click-retry behavior is not yet live-tested on the installed app.
- The repaired Stop behavior is not yet live-tested with two queued flows.

## Next Step

After the change reaches `origin/main` and explicit approval is given, click
Update App in Metronome, rerun the nine-download flow, and confirm all four
weeks remain selected on download 9 before RUN.
