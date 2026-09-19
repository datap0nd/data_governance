# Automatic recorded month ranges: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: [#141](https://github.com/datap0nd/data_governance/pull/141).
- Evidence cutoff (UTC): 2026-09-19 22:27; final-head CI is pending.
- Tested code revision: `ccf1b0818aa5dbd940574fa314dc62632f2d8669` plus the documented correction that preserves repeated clicks; the PR will record the exact final head.
- Environment: Windows, PowerShell, checkout-owned Python 3.13.15 `.venv`.
- Overall finding: the focused parser run passed with automatic month-range recognition. The requested live pre-fix attempt reproduced the generic slider import and separately showed that the recording did not capture a download. Its repeated clicks came from the test operator retrying through remote-input lag and are not treated as a Metronome defect. The first full CI attempt had one unrelated polling timeout that passed an exact local retest; final-head CI remains pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup | `tools/check.ps1 -Mode Setup` | Baseline plus working changes; Windows, Python 3.13.15 | PASS; locked checkout environment created. | `.test-runs/20260919T215805700Z-48996-4049cf6e/result.json` |
| I-01, I-02, N-01, R-01; affected syntax | Plan's focused `tools/check.ps1 -Mode Verify` command. | Baseline plus initial working changes; Windows | PASS: 5 passed, 0 failed in 1.08 s; dependency, preflight and syntax checks passed. | `.test-runs/20260919T215839935Z-47892-0b5fecf0/result.json` |
| Final focused retest after inference-safety refinement | The same focused verifier command. | Baseline plus final working changes; Windows | PASS: 5 passed, 0 failed in 0.49 s; dependency, preflight and syntax checks passed. This supersedes the initial focused result. | `.test-runs/20260919T220007833Z-18248-6da5d449/result.json` |
| Final focused retest after removing unproven click cleanup | The plan's focused verifier command. | Slider implementation with ordinary clicks preserved; Windows | PASS: 5 passed, 0 failed in 0.50 s; dependency, preflight and syntax checks passed. This is the current focused result. | `.test-runs/20260919T222505623Z-34260-2fcf5ac6/result.json` |
| First full CI attempt | Full `Tests` workflow on `ccf1b0818aa5dbd940574fa314dc62632f2d8669`. | Ubuntu shards, Windows verifier and frontend contracts | FAIL: shard 1 had 1 failed, 1143 passed, 17 skipped; the unrelated topic-group browser polling test exceeded its 20-second visibility timeout. Shard 0 and all other jobs passed. | [Run 35472156331](https://github.com/datap0nd/data_governance/actions/runs/35472156331) |
| First-CI exact failed-case retest | `tools/check.ps1 -Mode Verify -TestPath tests/test_flow_topic_groups_browser.py::test_actual_group_queue_polling_completion_failure_and_individual_recovery -SyntaxPath tests/test_flow_topic_groups_browser.py` | `ccf1b0818aa5dbd940574fa314dc62632f2d8669`; Windows | PASS: 1 passed with 2 existing deprecation warnings in 22.41 s. No application change was indicated. | `.test-runs/20260919T221702124Z-45152-c4059611/result.json` |
| L-01 | Record the requested monthly SMS raw-input flow in the authenticated work-PC session, inspect the imported slider step, then select **Test recording**. | Live pre-fix Metronome/portal session; Chrome Remote Desktop; recorder session retained by the application | FAIL: 107 steps imported. The month handle was generic Step 53, `Click “recorded element”`, from `.noUi-touch-area`. Repeated `MX` and `MIDDLE EAST` clicks were operator retry noise from remote-input lag, not a demonstrated recorder defect. Test recording stopped at the preflight message that no download was captured. No draft was activated. | Protected evidence `LIVE-SMS-20260920-01` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| C-01 | NOT RUN | No final PR head exists at this cutoff. | Push the reviewed commit, wait for `Merge ready`, and record its URL and exact SHA in the PR before merging. |

## Findings, limitations and retests

- The live failure is pre-fix evidence, not a claim about a deployed revision. The portal report itself reached visible monthly results, but the recorded flow omitted the real export/download trigger; Metronome correctly refused to test or activate it rather than fabricating an output.
- The implementation deliberately preserves all ordinary recorded clicks. The 107-step live attempt is corrected through test procedure—one click followed by visible-state inspection before any retry—not by guessing that repeated application actions are disposable.
- The implementation promotes only one unambiguous noUi touch-area click. It uses the nearest recorded month/monthly or week/weekly wording within the preceding actions; weekly, missing and multiple distinct noUi targets stay untouched.
- The generated action uses automatic two-handle discovery and oldest/latest selectable month calculations already covered by the existing version-5 runtime. Its `source_step` keeps the original click for editor recovery.
- A bounded review covered false-positive containment, nested recorded events, version/capability promotion, parameter contracts and ordinary-import regression. The earlier duplicate-click proposal was removed because its premise was not demonstrated as an application defect.

## Merge evidence

Final-head `Merge ready` is pending. Its run URL, exact tested head SHA and result must be added to the PR testing section before a head-pinned merge. This report intentionally does not represent pre-merge evidence as deployment verification.
