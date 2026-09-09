# Save recorded Flow without testing: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-09 before final-head commit and CI
- Tested code revision: uncommitted changes over `33619375f4b9db6eb2058f12e4e2b461ba0c0227`; final tested SHA will be recorded in the PR.
- Environment: Microsoft Windows 11 Home, Python 3.13.15 ARM64, Node v24.19.0, Playwright 1.62.0/Chrome headless fixture.
- Overall finding: changed behavior and frontend regressions pass locally. The first full Python run found one wording compatibility failure and one transient shared process-lock collision; both were corrected/cleared and passed targeted retest. Final-head full Python and GitHub CI are pending. Work-PC portal checks are BLOCKED.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| UI-01–UI-03 | Clickable fictional-data preview at `app/static/recording-preview/save-without-test.html`; exercised cancel and confirm states. | Uncommitted preview over baseline; local browser, 2026-09-09 | PASS. Cancel preserved edits; confirm showed runnable/untested state and first-run guidance. Owner rejected the earlier inactive-draft journey and approved the single-confirmation journey in this task. | Task conversation and committed preview source. |
| API-01–API-03, REG-01, recording UI regressions | `PYTHONPATH=.; pytest -q tests/test_recording_journey.py tests/test_recording_visual_editor.py` | Uncommitted implementation, Windows/Python 3.13.15 | Test bodies completed with 19 passing progress marks; pytest later raised Windows error 1463 while resolving its generated `*current` temp links during session cleanup. | Local output; not counted as a clean suite result. |
| API-01–API-03, REG-01 | Targeted new API tests during development. | Uncommitted implementation | PASS: 2 tests. The same Windows pytest cleanup error occurred after results. | Local output. |
| REG-02 | Full Python suite with only pytest dead-symlink cleanup hooks disabled. | Uncommitted implementation | FAIL: 2 failed, 1,846 passed, 11 warnings in 950.81 s. One failure required retaining the word `validate` in a compatibility error; one portable optional-check case collided with an existing Flow process lock. | Local full-suite output. |
| REG-02 retest | Reran the failed recording lifecycle test and all parametrizations selected by `test_portable_replays_without_page_questions_and_honors_optional_rows`; local preview server stopped first. | Corrected uncommitted implementation | PASS: 4 passed in 36.18 s. | Local output. |
| UI-01–UI-03, frontend regressions | Ran every `tests/test_*.mjs` file. | Corrected uncommitted implementation, Node v24.19.0 | PASS: 23 files. | Local output. |
| Frontend syntax | `node --check` for `app.js`, `users.js`, `flow_run_log.js`, `flow_recordings.js`, `flow_recording_editor.js`, and `flow_recording_model.js`. | Corrected uncommitted implementation | PASS: 6 files. | Local output. |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| REG-02 final-head full Python | NOT RUN | Final commit did not exist at this report cutoff. | Run the full suite on the committed head and record the result in the PR before merge. |
| GitHub Windows/Ubuntu CI | NOT RUN | PR not yet opened at this report cutoff. | Wait for required checks on final head and record run URL/SHA in the PR. |
| LIVE-01–LIVE-02 | BLOCKED | The UGREEN-25854 capture device was detected, but the required local capture page reported **Unable to play media** after reload, so no reliable work-PC screen evidence was available. Portal authentication/output checks were not attempted. | After merge/deployment, restore the capture feed and execute both ASAP and GSCM cases with protected evidence. |

## Findings, limitations and retests

The owner explicitly requested that testing not be mandatory when creating many Flows. The reviewed journey therefore uses one contextual confirmation at Save and makes the selected recording runnable. It does not silently waive testing: the client sends an explicit flag only after confirmation, while the server still performs structural safety validation and freezes the current configuration, transformation, and execution-core identity.

The original full-suite failures are retained above. The wording failure was fixed by preserving the existing `validate` compatibility term while adding the new approval path. The portable lock failure was transient environmental contention; it passed after stopping the local preview server and rerunning every selected parametrization. Final full-suite evidence is still required on the committed SHA.

Synthetic tests prove state handling, transactionality, and job construction but do not prove live SSO, portal navigation, report correctness, or downloadable output. Those checks remain BLOCKED rather than inferred.

## Merge evidence

Pending. The PR testing section must record the final GitHub Actions run URL, final head SHA, and Windows/Ubuntu results before merge. The PR merge record will supply the actual merge SHA; this report does not claim deployment verification.
