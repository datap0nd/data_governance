# Visual recorded-flow editor: test report

Environment: local Windows ARM64, Python 3.13, Playwright 1.62.0 and installed
Chrome. Implementation based on recording-v2 commit 24f96846. Final committed SHA
and Windows/Linux CI evidence are recorded in the PR testing section.

## Automated evidence at report cutoff

- Visual model Node suite: PASS (atomic groups, stable IDs, removal cleanup,
  rejected page dependencies and progress owner mapping).
- Initial browser suite: 5 passed / 1 failed (wait insertion used randomUUID on
  an HTTP page). Fixed using crypto.getRandomValues, supported on HTTP origins.
- Second browser run: 7 passed / 1 failed (move used object identity after an
  edit). Fixed movement to resolve the current index by stable ID.
- Subsequent six-case visual browser run: **6 passed in 26.78s**.
- Existing recording review/control browser tests: **2 passed in 16.20s**.
- Desktop/mobile synthetic screenshots were visually inspected. Inputs were
  adjusted for consistent spacing; only synthetic Sales Report data was used.
- Final expanded visual/runtime focused tests: **14 passed in 58.19s**, including
  compound groups, iframe title candidates, native-export marking, drag movement,
  page dependency rejection and output-validation failure attribution.
- All **22 Node test files** passed; recording editor/model syntax checks passed.
- Full local Python regression before final identity refinements: **1536 passed,
  12 existing Starlette/httpx deprecation warnings in 386.77s** (visual-full.txt).
- Final identity/visual/browser/portable focused retest: **22 passed in 72.06s**.
  Generic portal/download names and nested button text cannot prove report identity.
- Capability/model retest: **11 passed in 2.06s**, including v1 workers being unable
  to claim v2 run or recording jobs while v2 workers can claim them.
- Final-head Windows/Linux CI remains pending at this committed report cutoff;
  exact full-suite results, SHA and run URL are recorded in the PR before merge.

## Live work-PC evidence

VIS-01 through VIS-12 and the representative GSCM/ASAP pilot: **NOT RUN**. No live
portal or SSO session was exercised. Local synthetic passes do not establish
production reliability or hardware capacity. GitHub monitoring remains paused.

Commands: pytest tests -q --tb=short; pytest tests/test_recording_visual_editor.py
-q; node tests/test_recording_visual_model.mjs; all tests/test_*.mjs individually;
node --check for app.js and all recording JavaScript files. Local pytest uses a
fresh system temporary directory and disables only its optional latest-directory
symlink, unavailable to this Windows account. Detailed local logs are not
committed because the report summarizes evidence without publishing traces.

Final progress-retention review: terminal test messages no longer erase card
outcomes; cancelled/incomplete steps do not remain labelled Running. Related
model/API/recording tests: **43 passed in 28.62s** (visual-progress-final.txt).
This refinement is included in the final PR-head CI, not the earlier full local
1536-test run.

A second complete local suite at c225ae94 passed **1544 tests, 10 existing warnings
in 343.30s** (final-head-full.txt). This predates final progress-retention and
worker-version guards, which are covered by the focused checks and final CI.

Final recording/browser/portable contract run overlapped that complete suite:
74 passed and one portable Chrome case was blocked by the shared execution lock.
After the other process finished, the same set ran in isolation: **75 passed in
86.28s** (editor-contract-isolated.txt). The lock was retained; it was not bypassed.
Validation workers now check their actual execution hash before authentication,
and the server requires their engine-check capability for new validation jobs.

Final responsive/reconnect browser retest: **10 passed in 36.99s**
(responsive-final.txt). Resizing moves the existing details panel without losing
edits, and a recovered poll clears its temporary connection error.
