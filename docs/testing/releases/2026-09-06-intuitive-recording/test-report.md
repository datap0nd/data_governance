# Intuitive recording — test report

Implementation based on main `4f4a7022`; final tested head and Windows/Linux CI
run are recorded in the PR after checks complete. This file records the local
verification cutoff before that final commit.

The owner rejected the first preview's verbose checks form and approved the
minimal revision with “implement”. Preview lives in `app/static/recording-preview/`.
It uses fictional data and is not runtime/portal evidence.

Local environment: Windows ARM64, Python 3.13, Node, Playwright Chrome.
Temporary test storage is outside the checkout. The previously documented local
pytest latest-directory symlink helper is disabled because Windows cannot follow
that link type. Application checks are unchanged; CI runs ordinary pytest.

- Visual editor browser tests: 9 passed after adapting the old modal/activation
  expectations to full-page review without an Enable schedule action.
- Builder payload and visual model Node checks: passed.
- Full local Python run: 1,551 passed, 1 failed, 12 warnings in 654.18s.
  The process collected the older journey fixture before the title-page choice
  became automatic; it attempted to select the newly hidden field. The updated
  API/browser suites subsequently passed (15 tests), and final API/transform/
  legacy-evidence checks passed again (4 tests). This local run is not represented
  as a clean final-head full-suite pass; Windows/Linux CI is required before merge.
- All 22 Node suites passed; app.js, flow_recording_editor.js, flow_recordings.js
  and flow_recording_model.js syntax checks passed; git diff --check passed.
- Warnings: local Starlette TestClient/httpx deprecation and 11 existing timeout
  argument warnings in parallel-worker tests. No live result is inferred.
- Actual API browser journey: 8 recorder-import steps, incomplete draft saved,
  contextual missing-check resolution, private worker validation with real CSV
  download, return with pending form retained, atomic apply and selected-job
  construction. Synthetic worker authentication/launch are replaced; API handlers,
  SQLite, recording model and validation/execution engine are real.
- Browser layout evidence: [desktop](evidence/desktop.png),
  [laptop](evidence/laptop.png), [narrow](evidence/narrow.png), captured on the
  implementation worktree using the real API journey. Original screenshots
  contained inherited styling issues; these final images were visually reviewed.
- Commands: `python -m pytest tests -q --tb=short` (using the local symlink helper
  workaround and a temporary root outside the checkout), followed by
  `python -m pytest tests/test_recording_journey.py tests/test_recording_visual_editor.py -q`
  and `node tests/test_*.mjs` run individually in a loop. Final-head CI uses the
  repository workflow without the local workaround.
- Initial raw-import API regression: incomplete draft saves successfully; test
  rejects with 422 “Choose a report identity locator and its exact expected text
  before validation.” This is synthetic reproduction of the missing-check path,
  not reproduction of the original live Save/Test failure.
- Work PC, real SSO, original flow execution/output comparison: NOT RUN. A live
  authenticated work-PC session and post-deployment verification are still needed.

The final PR testing section records final-head CI results. No live success is
inferred from synthetic/browser fixtures.
