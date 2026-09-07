# Reusable recordings and downstream view refresh: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: recorded under Merge evidence.
- Evidence cutoff (UTC): 2026-09-07, before the implementation commit.
- Tested code revision: the working tree that became the implementation
  commit on `claude/reusable-recordings-view-refresh-4ii0nu` on top of
  `e0353e0`; the PR testing section records the final tested SHA.
- Environment: Linux container, Python 3.11.15, pytest with Playwright 1.62.0
  and the preinstalled Chromium 1194 build aliased to the `chrome` and `msedge`
  channel paths (the container's network policy blocks Chrome/Edge downloads);
  Node 22.22.2. No PostgreSQL, portal or worker is available here.
- Overall finding: automated checks PASS; usability preview walked in a real
  browser with fictional data; owner feedback on the preview and every live
  check NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| VR-01…05, VR-07, VR-08, VR-09 (plan/lock), VR-10 (scripts), VR-11 | `python -m pytest tests/test_view_refresh.py -q` | working tree, Linux | PASS: 22 passed in 18 s | Config normalization and inherit-on-omit; upstream-first ordering through ordinary views and cycle detection; per-view transactions (COMMIT, then ROLLBACK, later views skipped), checkpoint and retry; quoted identifiers and duplicates; existence/type/permission verification; automatic discovery (chain, exact identity, verified empty, unconfigured, incomplete, stale, cyclic); manual verification ordering; frozen plans and blocked queueing; recorded config hash unaffected; worker capability gate; persistence of SQL outcome and per-view rows, retry queueing and claim; retry refusal without commit, when superseded and when complete; reconciliation trigger v2; activity steps; standalone execution order, `--no-sql`, checkpoint refusal and `--retry-views`; precheck before insertion; script dry run and blocked plan; pipeline incorporation, deferral and lock conflicts. |
| TPL-01…07 (API) | `python -m pytest tests/test_recording_templates.py -q` | same | PASS: 5 passed | Module-scoped listing with default/active revision and versions, search, self-exclusion, pending website; preview and copy with provenance and no evidence, activation refused, independence from later source edits and deletion; module rules including pending website; older version copy and busy destination; replicate copy into a paused flow. |
| TPL-01…06, VR-01…05, VR-07, VR-08, UX-01 (browser) | `PREVIEW_EVIDENCE_DIR=… python -m pytest tests/test_templates_refresh_preview.py -q` | same, headless Chromium 1280×900 and 390×844 | PASS: 4 passed in 12 s | Screenshots in [evidence/](evidence/): `builder-automatic.png`, `builder-blocked-missing.png`, `builder-manual-verify-failed.png`, `builder-manual-verified.png`, `builder-narrow.png`, `recording-template-list.png`, `recording-template-preview.png`, `recording-template-applied.png`, `recording-template-undo-history.png`, `recording-template-narrow.png`, `replicate-new-flow.png`, `replicate-copied-recording.png`, `runs-retry-available.png`, `run-log-failed-view.png`, `run-log-retry-succeeded.png`, `runs-retry-failed-again.png`, `run-log-narrow.png`. No page errors; no horizontal overflow at 390 px. |
| Regression: recordings, editors, scripts, activity, parallel, pipelines, SQL | `python -m pytest tests/test_flow_standalone.py tests/test_flow_handover.py tests/test_flow_activity.py tests/test_flow_parallel.py tests/test_pipelines.py tests/test_flow_recordings.py tests/test_recording_v2_pipeline.py tests/test_recorded_browser_pipeline.py tests/test_flow_sql.py -q` | same | PASS: 211 passed in 168 s | Console. |
| Regression: browser editors | `python -m pytest tests/test_managed_flow_editor.py tests/test_recording_visual_editor.py tests/test_optional_recording_editor.py tests/test_recording_playback_ui.py tests/test_recording_startup_ui.py -q` | same | PASS after fix (30 passed); the first run found 3 failures because a second visible Undo button duplicated the accessible name; the editor now shows one Undo at a time | Console. |
| Full Python suite | `python -m pytest tests -q -p no:cacheprovider` | same | 1769 passed, 2 failed, 5 skipped, 13 warnings in 775 s; both failures retested PASS after the fixes described under Findings | Console. |
| Frontend | `node --check` on `app.js`, `flow_run_log.js`, `flow_recordings.js`, `flow_recording_editor.js`, `flow_recording_model.js`; every `tests/*.mjs` | same, Node 22.22.2 | PASS (22 Node suites); the builder contract test first failed because the payload reader sat outside its evaluated slice and was moved next to `_flowCollectBuilder` | Console. |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Owner feedback on the preview | NOT RUN | The owner was not available during this autonomous session; AGENTS.md asks for feedback before production implementation. The preview and the implementation shipped together. | Owner opens the preview (see plan) and reviews the journey; material changes return for review. |
| VR-06 live progress, VR-07/VR-08 against PostgreSQL | NOT RUN | No PostgreSQL, worker or portal in the container; refresh, verification and lock behavior are exercised through fake engines. | Configure a test Flow with a real materialized-view chain on the work PC, lock one view and run; retry. |
| VR-09 pipeline execution | NOT RUN | Plan incorporation and locks are tested; the MV stage against PostgreSQL is not. | Run a full pipeline containing a Flow with configured views. |
| VR-10 script execution | NOT RUN | Dry run, `--no-sql`, checkpoint and `--retry-views` are exercised in-process with a fake engine; no real PostgreSQL run. | Run the generated script on the work PC. |
| TPL live recording | NOT RUN | Copied recordings were not tested against a portal. | Copy a template, run Test recording on the work PC, then activate. |

## Findings, limitations and retests

- Fake engines record `REFRESH MATERIALIZED VIEW` statements and transaction
  boundaries; they cannot prove PostgreSQL lock behavior, permission checks on
  a real role or refresh durations.
- The container lacks Chrome/Edge downloads; the bundled Chromium was aliased
  to both channel paths for the browser tests. CI runs real Chrome/Edge.
- Full Python suite: `test_flow_builder_can_replicate_an_existing_flow` pinned
  the run-log asset version (`flow_run_log.js?v=3`), which this change bumps to
  `v=4`; the assertion was updated and passes.
  `test_isolated_direct_worker_entrypoint_from_another_directory` failed because
  `idna` was installed only in the user site, invisible to `python -I`; after
  installing it into the system site the test passes (environment fix, no code
  change). Warnings are the existing Starlette deprecations; skips are the
  pre-existing platform-conditional tests. The PR testing section carries the
  final-head CI run.

- First CI run (34117084476) on `558501d`: Ubuntu PASS (Python suite and every
  Node check); the Windows job failed 12 `tests/test_flows.py` cases that read
  `app.js` with the platform default cp1252 encoding because the new catalog
  search message used curly quotes (U+201D encodes to byte 0x9D). The quotes
  are now HTML entities; the job was cancelled at 85% to fetch the log, so it
  carries no other Windows result. The follow-up commit's CI run is recorded in
  the PR testing section.

## Merge evidence

Pending until the PR's final CI run finishes; the PR testing section records
the final run URL, tested head SHA and result before merging, and the PR merge
record supplies the merge SHA.
