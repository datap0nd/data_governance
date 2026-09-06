# Recording playback, timing and text diagnostics: test plan

- Scope: confirmed GSCM navigation, honest generic click status, positive per-action waits, and copyable test diagnostics.
- Baseline: GitHub main `51946f922a849ed3fc462e4c6f30f927703be51f`.
- Results: [test-report.md](test-report.md). Final tested head and CI links are recorded in the implementation PR before merge.
- Environments: Windows and Linux Python 3.13, Playwright 1.62.0, Chrome/Edge synthetic fixtures; separate authenticated work-PC portal checks.

## Prerequisites

Update both the app and worker from the merged main revision. Finish or cancel any active recording test before updating. The existing execution-engine check may require one test with the updated engine; no re-recording or mandatory page-readiness questions are introduced. Record the original Flows timing preference before changing it.

Use fictional data in the local recording preview and automated HTTP fixtures. Live tests use the user's existing recording in Test recording, which writes private validation outputs and disables SQL. Do not publish report contents, credentials, private addresses or paths as test evidence.

## Cases

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| RP-01 | Open Flows settings. Observe the default, change it to 12, save and reload. Try 0, -1, 1.5 and 601. Restore 10. | Starts at 10 on an unconfigured installation; positive whole seconds 1–600 persist; invalid values cannot save. |
| RP-02 | Open a recorded click. Select Use default, then Custom 60, save, move the step, undo, reload and wrap/unwrap a download action. | Wait before this step stays on the interaction; custom 60 replaces the default; no duplicate delay on event wrappers. |
| RP-03 | Test two clicks with default 10 and override 2. Test explicit waits of 5 and 60 before an inherited click. Cancel during a long wait. | First click waits 10; next waits 2; explicit waits add only missing buffer time; countdown visible; cancellation stops before dispatch. |
| RP-04 | Run synthetic inert caption, delayed ordinary click, missing native handler, duplicate/hidden control, replaced frame and empty Public cases. | Known navigation is confirmed or fails at that step; only the recorded frame/control is used; no bookmark rows required; no blind repeated downloads. |
| RP-05 | Test popup/download groups with long and nested intentional waits. | Listener stays armed, wait budget is added to the event allowance, each trigger dispatches once. |
| RP-06 | Open Debug log after success, failure and worker loss. Copy the text. Edit the recording and start another test with the old log open. | Copyable text retains the selected run's frozen labels and timing; missing/omitted evidence is identified; edits are preserved. |
| RP-07 | Use diagnostic fixtures containing entered values, credentials, URL secrets and filesystem paths. Request a test log through another Flow ID. | Sensitive values are absent; report is bounded; cross-Flow request is rejected. Diagnostic/trace cleanup failures cannot replace the original error. |
| RP-08 | Change global wait after testing a Flow; queue another test/run, resume an old run and inspect a portable export. | Future jobs use the new preference; old snapshots/resumes/exports retain their timing; preference alone does not invalidate recording evidence. |
| RP-09 | Walk settings, step delay, generic Click sent, confirmed navigation, optional download/data checks, failed test and log-copy recovery in the fictional clickable preview. | Minimal controls, Advanced stays collapsed after failure, clear inline feedback, no mandatory checks or folder choice. Text/DOM evidence only. |
| RP-LIVE | On the updated work PC, test the previously failing Setting → Public recording five times in the configured browser. Copy each debug log and note whether the actual panel/tab changed. | Five observed navigation transitions and complete expected private downloads. Record UTC, app/worker SHA, browser version, run IDs and sanitized findings for each attempt. Synthetic results do not satisfy this case. |

## Automated checks

Canonical CI setup: `.github/workflows/tests.yml`. Install its dependencies and browsers first.

```powershell
python -m pytest tests -q
Get-ChildItem tests/test_*.mjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw "Frontend check failed" } }
node --check app/static/app.js
node --check app/static/flow_recordings.js
node --check app/static/flow_recording_editor.js
node --check app/static/flow_recording_model.js
```

Run affected pacing, click, diagnostics and editor tests first for fast feedback. The full Python suite is required for the shared runtime; final Windows/Linux CI must pass on the PR head before merge.

## Acceptance and cleanup

Automated acceptance requires the behavioral and negative cases above, full Python and frontend checks, and final-head CI. RP-LIVE remains NOT RUN until authenticated work-PC evidence is collected. Keep synthetic browser results distinct from live results.

Restore the user's preferred global wait after testing and remove only the designated fictional preview/test outputs if desired. Preserve existing Flow definitions, stable published outputs, and unrelated work. If live navigation still fails, copy the Debug log from that attempt; do not repair arbitrary selectors or add mandatory readiness assertions to conceal the failure. Rollback uses the standard updater's previous tested app/worker revision together.
