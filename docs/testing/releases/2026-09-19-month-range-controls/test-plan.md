# Monthly range controls in recorded Flows: test plan

- Change/PR: pending.
- Baseline: `origin/main` `7e2d85e93af62219649212d8adecf2a830b246eb`.
- Related report: [test-report.md](test-report.md).
- Scope: recording definition version 5; `YYYYMM` month parameters; oldest/newest selectable month behavior; automatic discovery of the nearest ancestor containing exactly two visible slider handles; editor controls and worker capability gating. Existing version 1–4 recordings remain supported.

## Prerequisites and test data

Use the checkout-owned Python 3.13 `.venv` created by `tools/check.ps1 -Mode Setup`. Synthetic playback uses a keyboard-driven, noUi-shaped two-handle slider in automated Chrome/Playwright with values `202601` through `202609`; no visible preview is opened. Recording API cases use disposable SQLite data and isolated managed-flow folders.

The owner explicitly requested one live baseline attempt for **Retail → SMS Monthly Performance → Export Wizard (Product)**. Protected portal details and raw downloads must not be committed; record only the opaque evidence identifier and sanitized observations.

## Cases

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| C-01 | Validate version-5 month definitions with start/end month parameters; try version 4, mismatched week parameters, invalid `202613`, unsupported kind and offsets. | Version 5 accepts valid `YYYYMM`; older/mismatched/invalid contracts fail with a specific validation error. |
| C-02 | Resolve fixed, current and previous month values; cross `202612` to `202701`. | Months parse and move one calendar month at a time; fixed values preserve `YYYYMM`; live extremes remain unresolved until playback. |
| C-03 | Queue version-5 work for a v4 worker, then advertise `recorded_flows_v5`. | The old worker cannot claim the job; the v5 worker can. Version-4 week work retains its v4 gate. |
| P-01 | Start with handles on `202608`/`202609`; target a nested touch area; use automatic container detection and oldest/newest behaviors. | Playback finds the smallest ancestor with exactly two visible handles, sends lower to Home and upper to End, verifies `202601`/`202609`, and records both live parameter values. |
| P-02 | Provide only one visible handle or an invalid/unreachable month. | Playback fails before any download; it never guesses coordinates or reports success without exact read-back. |
| P-03 | Run existing week/date slider cases, including async settling, declared limits, portal defaults, fixed/calculated values and cancellation. | Existing version-4 behavior remains unchanged. |
| E-01 | In the automated editor journey, convert one recorded handle click to a range control and select **Months (YYYYMM)**. | The card reads **Set month range** with a **Month range** badge; Element box defaults to automatic; start defaults to oldest selectable, end to newest selectable, both offsets are zero, and week-start controls are absent. |
| E-02 | Save the draft and inspect its definition. | Version 5 contains kind `month`, automatic ancestor level `0`, restorable source click, and two month parameters using `%Y%m`. |
| E-03 | Exercise the existing week/date authoring controls and a 390×844 viewport through automated Playwright. | Existing validation and restore behavior remain; no horizontal overflow. No visible preview is presented to the owner, per their instruction. |
| L-01 | Baseline live attempt: record the requested SMS raw-input export and manually expand the month slider fully before running it. | Sanitized evidence records whether recording captures the slider semantically and whether the portal can export with the full range. This diagnoses the pre-fix behavior; it is not post-fix deployment evidence. |

## Automated verification

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath @('tests/test_recording_sliders.py','tests/test_recording_slider_editor.py') `
  -SyntaxPath @('app/flow_range_slider.py','app/flow_recording.py','app/flow_recording_runtime.py','app/flow_recordings.py','app/flow_worker.py','app/routers/flow_recordings.py','app/routers/flows.py','app/static/flow_recording_editor.js','app/static/flow_recording_model.js','tests/test_flow_recordings.py','tests/test_recording_controls.py','tests/test_recording_slider_editor.py','tests/test_recording_sliders.py','tests/test_recording_startup.py','tests/test_recording_visual_model.mjs','tests/test_sql_ownership.py')
```

Final-head `Merge ready` is the authoritative full Python, frontend and Windows regression. Record its run URL and exact head SHA in the PR before merging.

## Acceptance, recovery and cleanup

Accept when the focused run passes, the bounded diff review finds no open failure/recovery issue, and final-head CI passes. Playback must stop before download when it cannot identify exactly one two-handle control or cannot prove the requested bounds.

Rollback is a PR revert. Older workers refuse version-5 definitions by capability gate. Existing version 1–4 recordings continue to validate and run. Test databases, browser pages and isolated flow folders are temporary verifier artifacts.
