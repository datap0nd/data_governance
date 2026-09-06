# Recording playback, timing and text diagnostics: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-06 13:09 UTC for the focused checks below; full-suite and final-head CI results are pending at this initial cutoff.
- Tested code: working-tree implementation on baseline `51946f922a849ed3fc462e4c6f30f927703be51f`; exact UI source hashes are in [browser observations](evidence/ui-observations.json). The final tested head is recorded in the implementation PR before merge.
- Environment: Windows 11 build 26200, Python 3.13 ARM64, pytest 8.3.5, Playwright 1.62.0, Chrome 152.0.7977.76. Browser fixtures use fictional local HTTP data.
- Finding: focused automated checks pass. The full regression is in progress. Live GSCM/work-PC execution is **NOT RUN**.

## Executed checks

| Check | Command / procedure | Actual result | Evidence |
| --- | --- | --- | --- |
| Timing | `pytest tests/test_recording_pacing.py -q` with the host-only wrapper below | 17 passed, 15.88 s. A real browser waited the initial 10 seconds and the custom 2 seconds and dispatched each click once. | Local focused run; final full suite also covers the added nested-countdown assertion. |
| Recorded click behavior | `pytest tests/test_recording_clicks.py -q` | 22 passed, 24.74 s. Exact-frame native recovery, delayed ordinary clicks, empty scopes, stale targets, cancellation, worker CSV download, and portable pipeline. | Synthetic browser fixture only. |
| Settings and diagnostics | `pytest tests/test_recording_timing_settings.py tests/test_recording_diagnostics.py tests/test_flow_capacity.py tests/test_recording_startup.py tests/test_recording_v2_model.py -q --tb=short` | 73 passed, 1 Starlette/httpx deprecation warning, 17.47 s. | Includes frozen/resumed waits, redaction, previous-action evidence, authenticated-start failure and worker loss. |
| Buffer-before-input regression | `pytest tests/test_recording_pacing_probes.py -q` | 2 passed, 0.27 s. Wait precedes locator/type/expected-text probes for fill and typing. | Synthetic unavailable-input test. |
| Root contract check | `pytest tests/test_recording_pacing_probes.py tests/test_recording_timing_settings.py tests/test_recording_diagnostics.py -q` | 22 passed, 1 Starlette/httpx warning, 3.39 s. | Independent integration check. |
| Related recording regression | `pytest tests/test_flow_recordings.py tests/test_recorded_browser_pipeline.py tests/test_recording_v2_pipeline.py tests/test_recording_optional_checks.py tests/test_recording_controls.py tests/test_recording_pacing.py -q --junitxml=test_reports/recording-playback-related.xml` | 94 passed, 160.17 s. | Local XML/log retained; run began before the final nested-countdown/pre-probe refinements, which have separate regressions. |
| New editor journey | `pytest tests/test_recording_playback_ui.py -q` | 6 passed, 19.48 s. | [Text/DOM observations](evidence/ui-observations.json), wide and narrow layouts; no screenshots. |
| Existing editor journeys | `pytest tests/test_optional_recording_editor.py tests/test_recording_visual_editor.py -q` | 19 passed, 46.99 s. | Existing optional checks and visual editing preserved. |
| Frontend models / syntax | All 22 `tests/test_*.mjs` suites; `node --check` for app, editor, model and playback preview | PASS; Node suite loop 3.29 s. Added unlabeled `get_by_title('Setting')` / `get_by_text('Public')` regression also passed. | UI source hashes in the observation artifact. |

## Full regression and CI

The local full Python suite was started after production changes were frozen. Its actual result will be appended before delivery. Required Windows/Linux CI must pass on the final PR head; its run URL and tested SHA will be recorded in the PR before merge. No passing CI result is claimed at this cutoff.

## Host-specific test setup and early failures

This Windows host disables following pytest's optional `*current` directory symlinks. A first focused command therefore failed during cleanup. A second attempt placed test Flow roots inside the checkout and correctly hit the managed-folder guard. Neither attempt is counted as a pass. Subsequent local database/browser suites use fresh temporary directories outside the checkout and disable only pytest's convenience symlink creation in the test process:

```powershell
python -c "import _pytest.pathlib as p,pytest,tempfile,uuid; p._force_symlink=lambda *a,**k: None; raise SystemExit(pytest.main(['tests','-q','--basetemp='+tempfile.gettempdir()+'/metronome-playback-full-'+uuid.uuid4().hex,'--junitxml=test_reports/recording-playback-full.xml']))"
```

No production path guard or operating-system symlink policy was changed. A parallel portable test initially encountered the shared test Flow execution lock; its fixture now uses an isolated lock directory. The completed click suite passed afterward.

## Unperformed live checks

| Case | Status | Reason / next action |
| --- | --- | --- |
| RP-LIVE: actual Setting → Public recording, five attempts | NOT RUN | No authenticated work-PC test has run on this implementation. After deployment, use the existing Test recording action and copy its debug log for each attempt. |
| Actual business-data output inspection | NOT RUN | Synthetic CSV/XLSX assertions cannot establish the actual portal report's correctness. Optional configured output checks remain available. |

The fixtures establish repeatable handling and honest diagnostics; they do not prove that the user's particular live failure has been resolved. Existing execution-engine validation remains, while changing the shared timing preference alone does not invalidate recording evidence.
