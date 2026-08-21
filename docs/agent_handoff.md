# Agent Handoff

## Current Objective

Stop ASAP HTML-dashboard flows from remaining in File Export for 30 to 40
minutes after Edge has already completed the selected download.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest base commit: `47f6ca5`
- Public repo: no, private
- Delivery target: scoped fix committed and pushed to `origin/main`
- Preserve unrelated working-tree changes in `app/flow_gscm.py`; they belong to
  another active task and are not part of this fix.

## Decisions Made

- Keep the 15-minute pre-response budget for genuinely slow dashboard exports.
- Subscribe to Edge download events on every existing portal page and every
  popup opened after the dashboard click, matching the established ASAP Export
  Wizard strategy.
- Pump Playwright events while waiting so delayed popup and download callbacks
  are dispatched instead of being starved by a folder-only `time.sleep` loop.
- Once Edge emits its native download event, require the local staging file to
  appear within 60 seconds. A missing browser-to-staging handoff should fail
  promptly instead of consuming the 15-minute budget on each task retry.
- Retain folder detection as a fallback for downloads that create a local file
  without exposing a Playwright download event.

## Files Changed

- `app/flow_worker.py`: event-aware HTML-dashboard download start and popup
  detection, future-page listeners, prompt post-event staging failure.
- `tests/test_flow_worker_discovery.py`: regression coverage for Edge events,
  staging-only completion, intermediate popups, and listener cleanup.
- `docs/agent_handoff.md`: this handoff.

## Commands And Checks

- Production read-only evidence: runs 181 and 182 both reached File Export
  within seconds, stayed there for about 40 minutes, recorded no file or phase
  timing, and ended only when the user stopped them.
- `/tmp/dg-flow-tests/bin/python -m pytest -q`: 397 passed.
- `/tmp/dg-flow-tests/bin/python -m compileall -q app`: passed.
- `node --check app/static/app.js`: passed.
- `node tests/test_flow_catalog_tree.mjs`: passed.
- `node tests/test_lineage_display.mjs`: passed.
- `node tests/test_lineage_edges.mjs`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- `git diff --check`: passed.
- Not run: a new production HTML-dashboard flow. Starting a download and SQL
  replacement is a production mutation and was not authorized during the
  read-only diagnostic.

## Open Questions

- The exact native Edge event and staging filename from the affected dashboard
  cannot be proven until one updated production run is explicitly authorized.
- If Edge reports the event but no local staging file appears within 60 seconds,
  the updated run will now fail with that specific handoff error rather than
  hang through repeated long waits.

## Next Step

After the scoped commit is present on `origin/main` and installed on the BI
desktop, explicitly authorize one affected HTML-dashboard flow run. Verify the
complete path: control discovery, Edge event, stable local staging file, saved
artifact, workbook normalization, SQL handoff, and successful run completion.
