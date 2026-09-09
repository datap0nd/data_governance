# Recording dialog import: test report

- Plan: [test-plan.md](test-plan.md).
- Change: [PR #88](https://github.com/datap0nd/data_governance/pull/88).
- Evidence cutoff: 2026-09-09, before the release-documentation commit and before full-suite/final-head CI completion.
- Tested implementation revision: `f3613c0babaffa2a77c8b42101402309928dbe71`. The initial broader test run began with exactly these source/test changes uncommitted against `33619375`; they were committed unchanged while that run continued. Later changes are release documentation only.
- Environment: Windows ARM64, Python 3.13.15, Playwright 1.62.0, Chrome 152.0.7977.76, Node 24.19.0.
- Finding at cutoff: targeted and broader recording regressions PASS; full local Python suite RUNNING; final-head CI PENDING; live ASAP NOT RUN.

## Executed checks

| Cases | Procedure | Result | Evidence |
| --- | --- | --- | --- |
| Baseline reproduction | Load `origin/main:app/flow_recording.py` from Git into an isolated Python namespace, insert the exact generated handler at line 9 of the fictional CODEGEN fixture, and call `import_codegen`. | Expected failure reproduced: `Unsupported action 'once' on line 9.` Baseline `33619375`. | Local console observation; no private recording used. |
| DI-01–DI-04 | Targeted command below. | PASS: 19 passed, 50 deselected in 13.08s. No reported warnings/skips. | Local console result on implementation SHA above. |
| DI-01–DI-04 plus surrounding recording, portability, acquisition, Finish/Cancel and recovery regressions | Broader command below. | PASS: 69 passed in 127.18s. No reported warnings/skips. | Local console result; includes actual Chrome alert/popup/two-download replay with fictional CSV data. |
| Patch hygiene | `git diff --check` | PASS; Git reports normal LF-to-CRLF working-copy notices. | Local console result. |
| Full shared Python regression | Full-suite command below. | RUNNING at this evidence cutoff; no passing outcome claimed. | Final outcome must be recorded in PR #88 before merge. |

Actual commands (the harness disables only pytest's unsupported local Windows
temporary-directory symlink operation; application code is unmodified):

```powershell
python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests/test_flow_recordings.py','tests/test_recording_controls.py','tests/test_recorded_browser_pipeline.py','-q','-k','scaffolding or callbacks or dialog_download or two_correlated','--basetemp='+tempfile.gettempdir()+'/recording-once-regression-'+uuid.uuid4().hex]))"
python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests/test_flow_recordings.py','tests/test_recording_controls.py','tests/test_recorded_browser_pipeline.py','-q','--basetemp='+tempfile.gettempdir()+'/recording-once-focused-'+uuid.uuid4().hex]))"
python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests','-q','--basetemp='+tempfile.gettempdir()+'/recording-once-full-'+uuid.uuid4().hex,'--junitxml='+tempfile.gettempdir()+'/recording-once-full.xml']))" > $env:TEMP/recording-once-full.log 2>&1
```

The full-suite log/XML stay local. Do not publish unsanitized suite logs. Final
aggregate results, warnings and CI links belong in the PR testing section.

## Unperformed checks and limits

| Cases | Status | Reason / next action |
| --- | --- | --- |
| DI-05 first ASAP recording and Finish | NOT RUN | Requires an authenticated work-PC session; update app/worker to merged main and follow the plan. |
| DI-06 live replay/output comparison | NOT RUN | Requires actual ASAP report and protected output verification. |
| DI-07 live cancel/retry | NOT RUN | Requires work-PC recording controls; synthetic lifecycle checks passed separately. |
| New UI preview/approval | N/A | No controls, labels or journey changes. |

The report verifies compatibility with Playwright 1.62.0's exact generated
dialog-dismiss statement, confirmed in the installed upstream code generator.
It does not claim support for arbitrary callbacks, custom dialog responses or
live portal success. Playback's default native-dialog handling is unchanged.

## Final CI and merge evidence

Final-head CI is PENDING at this committed cutoff. Before merging, PR #88 must
record the final tested SHA, CI run URL, completed results and local full-suite
outcome. Its testing section preserves evidence produced after this report;
its merge record supplies the actual main merge SHA. No post-deployment result
is claimed here. Append dated revision-specific live evidence when available.
