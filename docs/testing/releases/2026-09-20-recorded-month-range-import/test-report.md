# Automatic recorded month ranges: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: pending.
- Evidence cutoff (UTC): 2026-09-19 22:02; final-head CI is pending.
- Tested code revision: `d114ef5c3966096bddd63d7e0a895150b44ef130` plus the uncommitted implementation and tests described in this package; the PR will record the exact final head.
- Environment: Windows, PowerShell, checkout-owned Python 3.13.15 `.venv`.
- Overall finding: the final focused parser run passed. The requested live pre-fix attempt reproduced the generic slider import and separately showed that the recording did not capture a download. Final-head CI remains pending at this cutoff.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup | `tools/check.ps1 -Mode Setup` | Baseline plus working changes; Windows, Python 3.13.15 | PASS; locked checkout environment created. | `.test-runs/20260919T215805700Z-48996-4049cf6e/result.json` |
| I-01, I-02, N-01, R-01; affected syntax | Plan's focused `tools/check.ps1 -Mode Verify` command. | Baseline plus initial working changes; Windows | PASS: 5 passed, 0 failed in 1.08 s; dependency, preflight and syntax checks passed. | `.test-runs/20260919T215839935Z-47892-0b5fecf0/result.json` |
| Final focused retest after inference-safety refinement | The same focused verifier command. | Baseline plus final working changes; Windows | PASS: 5 passed, 0 failed in 0.49 s; dependency, preflight and syntax checks passed. This supersedes the initial focused result. | `.test-runs/20260919T220007833Z-18248-6da5d449/result.json` |
| L-01 | Record the requested monthly SMS raw-input flow in the authenticated work-PC session, inspect the imported slider step, then select **Test recording**. | Live pre-fix Metronome/portal session; Chrome Remote Desktop; recorder session retained by the application | FAIL: 107 steps imported; the month handle was generic Step 53, `Click “recorded element”`, from `.noUi-touch-area`. Test recording stopped at the preflight message that no download was captured. No draft was activated. | Protected evidence `LIVE-SMS-20260920-01` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| C-01 | NOT RUN | No final PR head exists at this cutoff. | Push the reviewed commit, wait for `Merge ready`, and record its URL and exact SHA in the PR before merging. |

## Findings, limitations and retests

- The live failure is pre-fix evidence, not a claim about a deployed revision. The portal report itself reached visible monthly results, but the recorded flow omitted the real export/download trigger; Metronome correctly refused to test or activate it rather than fabricating an output.
- The implementation promotes only one unambiguous noUi touch-area click. It uses the nearest recorded month/monthly or week/weekly wording within the preceding actions; weekly, missing and multi-click cases stay untouched.
- The generated action uses automatic two-handle discovery and oldest/latest selectable month calculations already covered by the existing version-5 runtime. Its `source_step` keeps the original click for editor recovery.
- A bounded review covered false-positive containment, nested recorded events, version/capability promotion, parameter contracts and ordinary-import regression. The candidate count was tightened after the first focused run so any second noUi handle click prevents automatic conversion; the clean final focused retest passed.

## Merge evidence

Final-head `Merge ready` is pending. Its run URL, exact tested head SHA and result must be added to the PR testing section before a head-pinned merge. This report intentionally does not represent pre-merge evidence as deployment verification.
