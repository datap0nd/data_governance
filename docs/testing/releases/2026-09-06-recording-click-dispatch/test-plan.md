# Recording clicks and technical debug logs: test plan

- Baseline: `08f0404a7f2177e7d7034f71d033f6027863f288` (PR #78).
- Scope: send recorded clicks once without inferred panel/selection assertions; keep waits in step settings; export useful, redacted technical diagnostics.
- Results: [test-report.md](test-report.md).

## Prerequisites

Update the app and its worker from the merged GitHub main revision. Restart the worker and confirm it has the same execution-engine version as the app. Use an existing recorded Flow with Setting → Public and a download, or a duplicate intended for testing. Leave schedules disabled. Note the original shared wait and step overrides before changing them.

The local preview at `/static/recording-preview/playback.html` uses fictional data and never starts a worker. Browser fixtures use local HTTP pages. Actual portal tests require the authenticated work-PC browser and access to the managed output location.

## Cases

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| CD-01 | Test a recording that opens Setting and clicks Public. The synthetic Public handler changes the available view without setting ARIA or native selection flags. | Each action reports **Click sent.** No selection polling, “Public was not selected” failure, or native second click. The next action and one download complete. Worker and portable fixtures both exercise this. |
| CD-02 | In the fixture, make Setting inert so the next Favorite target is absent. Also test hidden, disabled, duplicate and missing recorded targets. | Stop at the actual non-actionable target. Debug log identifies the failed call, timeout, locator, match count, exception signals and code locations, plus the preceding Setting dispatch. No blind retry. |
| CD-03 | Edit Public → Wait before this step → Custom → 60. Save, reopen, move and undo the step. Restore Use default. Change the shared preference in Flows settings. Try zero. | Custom replaces the default; default remains initially 10 and zero is rejected. No additional step appears. The list has no Add wait or insertion buttons. Existing saved standalone waits remain editable and retain their credit toward the next buffer. |
| CD-04 | Open Debug log after a successful test and a failed test; Copy debug log. Deny clipboard access in the fixture, retry a failed fetch, and start another test while the panel remains open. | Copy preserves the displayed test. Failure and fallback feedback is adjacent to the control; draft edits survive. Text contains frozen step IDs/locators, dispatch method/policy, timeouts, waits, structural candidates, frame identity/hash, exception signals and source file/line/function stack. |
| CD-05 | Inspect a legacy failed Public test and a worker-loss test; edit the current recording and read the old log again. Request the log through another Flow ID. | The old selection rule is explained; unavailable historical attributes are explicitly identified, never invented. Frozen steps remain accurate. Wrong-Flow access returns 404. Logs stay bounded, with omission markers. |
| CD-06 | Use synthetic entered values, credentials, URLs, paths and DOM contents in errors. Overlay a target and duplicate it. | Technical signals identify the blocking element and all sampled targets, but exclude entered values, credentials, page/report text, private URLs and paths. Missing ARIA attributes are shown as null, not false. |
| CD-07 | Add an optional minimum-row check to a download, fail it, remove it and test again. | Output checks remain optional. Download listeners remain armed through configured buffers; downloads fire only once. |
| CD-LIVE | After deployment, repeat the actual work-PC Setting → Public recording and inspect the newly downloaded file. Copy the debug log for each attempt. | Record app/worker revisions, test ID, browser, observed navigation, output result and a sanitized evidence reference. Automated tests do not establish this result. |

## Automated commands

```powershell
python -m pytest tests/test_recording_clicks.py tests/test_recording_pacing.py tests/test_recording_pacing_probes.py tests/test_recording_diagnostics.py -q
python -m pytest tests/test_recording_playback_ui.py tests/test_optional_recording_editor.py tests/test_recording_visual_editor.py -q
python -m pytest tests -q
node --check app/static/app.js
node --check app/static/flow_recording_editor.js
node --check app/static/recording-preview/playback.js
```

Run every `tests/test_*.mjs` file with Node and the repository's Windows/Linux CI on the final PR head. The report records the host-only pytest temporary-directory workaround if required.

## Cleanup and rollback

Restore the shared wait and step overrides, leave test schedules disabled, and remove only designated synthetic outputs. Keep private live evidence out of GitHub. To roll back, restore both app and worker to the baseline revision above and retest before enabling a schedule; that rollback restores the former selection gate. Engine compatibility checks remain in force.
