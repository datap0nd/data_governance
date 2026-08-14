# Agent Handoff

## Current Objective

Live-verify hover-safe, state-confirmed week selection on the nine-download ASAP
flow, then verify Stop behavior with two queued flows.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Runtime commit: `c6da87f Reject hovered ASAP week selections`
- Push status: runtime commit verified on `origin/main`
- Public repo: no, private
- Stable baseline: tag `asap-ui-automation-stable-2026-08-14` at `d2b61f1`
- Preserve untracked `governance.db-shm` and `governance.db-wal`; never stage them

## Decisions Made

- The unverified REST rewrite remains reverted. Runtime behavior uses the UI
  scraper restored from the stable baseline.
- Every scraper member selection is one ordinary left click with no keyboard
  modifier. The week and Dimension path explicitly uses one 100 ms left-button
  press-and-release.
- ASAP uses a blue background for both selected and hovered rows. The scraper
  now parks the pointer away from the prompt before reading state and refuses
  to use blue styling as selection evidence while a row or ancestor is hovered.
- A click is not successful until the non-hovered rendered state confirms it.
  Unknown state counts as missing, retries are bounded, and the exact requested
  set must remain stable across three reads before RUN is allowed.
- Dimension clearing reconciles across four rounds and uses the same hover-safe
  click confirmation.
- Every queued, claimed, or running flow renders Stop. Cancelling a queued run
  does not terminate another flow's worker; assigned runs target their exact
  headed or headless worker process.

## Files Changed

- `app/flow_worker.py`: use explicit single left press-and-release, move the
  pointer away after every click, and ignore blue styling while hovered.
- `tests/test_flows.py`: reproduce a dropped final week click whose hover looks
  selected, require a retry, and assert exact click button/count/delay options.

## Commands And Checks

- Live target evidence before `c6da87f`: in a flow starting at week 27, weeks
  27 and 28 were selected but the final clicked week 29 was omitted.
- Root cause: the pointer remained over the final row, and the verifier could
  mistake ASAP's blue hover background for a successful selection.
- Full Python suite: `192 passed`.
- Selection stress simulation: `20,000` randomized week cases passed with
  retained selections, zero through two dropped clicks, delayed rendered state,
  and hover false positives.
- `node --check app/static/app.js`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- `python3.11 -m py_compile app/flow_worker.py`: passed.
- `git diff --check`: passed.
- Control-modifier source audit: passed with no scraper Control clicks.
- Citrix remained read-only. Nothing was updated, run, downloaded, edited,
  saved, refreshed, applied, published, exported, sent, or deleted remotely.

## Open Questions

- Commit `c6da87f` has not yet been installed or live-tested inside ASAP.
- The repaired Stop behavior has not yet been live-tested with two queued flows.

## Next Step

With explicit approval, click Update App in Metronome, rerun the nine-download
flow, and confirm the final configuration retains all requested weeks before RUN.
