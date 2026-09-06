# Recording clicks and technical debug logs: test report

- Plan: [test-plan.md](test-plan.md).
- Baseline: `08f0404a7f2177e7d7034f71d033f6027863f288`.
- Tested code: uncommitted implementation on `codex/recording-click-dispatch`; source hashes and actual DOM observations are in [evidence/ui-observations.json](evidence/ui-observations.json). Final-head CI is recorded in the PR before merge.
- Environment: Windows 11, Python 3.13 ARM64, Playwright 1.62.0; synthetic Chrome/local HTTP fixtures.
- Evidence cutoff: pending full local regression completion.

## Executed checks

| Check | Actual result | Evidence |
| --- | --- | --- |
| Clicks, pacing, pre-buffer probes and diagnostics | **62 passed, 1 existing Starlette/httpx deprecation warning, 45.41 s** | Commands in plan; local `test_reports/recording-click-final-focused.log`. Includes worker and portable downloads with no Public selection markers. |
| Production editor journeys | **25 passed, 66.41 s** | Playback, optional-check and legacy-editor suites. Includes custom waits, legacy waits, copy/failure/recovery and narrow layout. |
| Frontend | **22 Node suites passed**, 2.81 s; app/editor/preview syntax passed | Every `tests/test_*.mjs` file executed. |
| Manual fictional preview | **PASS** | In-app browser DOM: five steps remained five after Public wait changed to 60; no insertion controls; Click sent results; Advanced collapsed; technical text copied successfully. 655×632 viewport with no horizontal overflow. |
| Full local Python suite | PENDING | `test_reports/recording-click-full.log` and XML. |
| Final Windows/Linux CI | PENDING | Final head/run/results must be recorded in the PR before merge. |

## Earlier checks and corrections

The first runtime-focused run had 38 passes and four test failures: one fixture incorrectly assumed an owning-button center could not hit an inert child; one expectation still required the former `unconfirmed` label; two pre-buffer probe assertions did not account for the new post-failure diagnostic read. A second diagnostics/click run had 42 passes and the same fixture failure before its correction. The fixture now positions the inert caption away from the owner center, and the assertions verify the current dispatch status and diagnostic read order. The final 62-test run above passed. No production force-click or native retry was added to satisfy the fixture.

This Windows host cannot follow pytest's optional convenience symlinks. Local suites use fresh temporary directories outside the checkout and disable only that pytest helper:

```powershell
python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests','-q','--basetemp='+tempfile.gettempdir()+'/metronome-dispatch-full-'+uuid.uuid4().hex,'--junitxml=test_reports/recording-click-full.xml']))"
```

## Live results and limitations

The owner reported that the prior release clicked Setting and Public, but its Public selection check rejected the transition. The exact deployed worker revision and original log were not supplied; this is owner-reported evidence, not a reproduced live test.

**CD-LIVE: NOT RUN** on this implementation. No authenticated work-PC test or business-data output inspection has run. After updating both app and worker, use Test recording and copy the new debug log. Old logs can explain the old aggregate rule but cannot recover DOM attributes or stack frames that were never recorded.

The new behavior deliberately verifies browser dispatch only. An application may ignore a sent click; a missing subsequent target or an optional output check can expose that problem. Synthetic fixture success is not proof of live portal correctness.
