# Recording v2: results report

Evidence cutoff: implementation working tree based on 3121f69a, Windows ARM64,
Python 3.13, Playwright 1.62.0, installed Chrome. This report does not claim live
portal or deployed-worker verification.

- Focused Python: 51 passed in 59.01s across retirement, multi-output portable
  pipeline, existing recordings and recorder controls. Evidence: local v2-focused.txt.
- Model: 9 passed in 0.68s (wait duration/cancellation, identity provenance and pages).
- Frontend: all 21 Node test files passed; recording editor syntax passed.
- Full Python suite: **1527 passed, 10 warnings in 335.31s**. Evidence: local
  v2-full.txt. Warnings are existing Starlette TestClient/httpx deprecations.
  Progress-map and readiness dependency additions made during that run receive
  the focused retest below; final-head Windows/Linux CI is recorded in the PR.
- Live work-PC GSCM/ASAP pilot, SSO and hardware checks: **NOT RUN**. No live session
  was exercised. Synthetic downloads do not establish portal reliability.

Commands: pytest tests -q --tb=short; focused modules listed in test-plan.md;
node tests/test_*.mjs (each file); node --check app/static/flow_recordings.js.
Local pytest uses a fresh system temporary directory and disables only pytest's
latest-directory symlink, which is unavailable to this Windows account.

Final focused retest after progress/dependency additions: **16 passed in 37.32s**
(test_recording_v2_model, test_recording_v2_pipeline, test_recorded_browser_pipeline).
Evidence: local v2-final-focused.txt.
