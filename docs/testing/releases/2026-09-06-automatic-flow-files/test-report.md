# Automatic Flow files: test report

[Plan](test-plan.md) · [PR #80 and subsequent CI evidence](https://github.com/datap0nd/data_governance/pull/80). Evidence cutoff: 2026-09-06 UTC, before completed GitHub CI results. Runtime/UI code revision: `3d20e74b312f9b2ccdc4d93dc42efc046a1f75a0`. Updated regression assertions: `dea773e66ff12d0744dbd6be582b00789620838f` (tests only). The subsequent documentation commit changes no execution code.

Environment: Windows 11 ARM64, Python 3.13.15, pytest 8.3.5, Node 24.19.0, Playwright 1.62.0. All 22 Node suites and `node --check app/static/app.js` passed. Targeted handover tests: 11 passed in 5.57s. Auto-update lifecycle plus earlier 9 handover tests: 72 passed, 1 pre-existing Starlette/httpx deprecation warning, 60.82s.

An initial full run was stopped after two setup/teardown errors. Isolation produced 80 passed and one Windows DB-file teardown error in 11.52s: the read-only synchronization connection was not closed. The code now closes it explicitly; the 72-test retest above passed. An intermediate quiet run was interrupted while investigating output buffering; it is not counted as a completed test run.

Browser preview HF-07: saved a renamed Flow, opened Flow files and folder listing, closed it, simulated unavailable share, inspected failure status, restored access and saved again. Edits were retained. Final DOM showed only Flow files/Open folder/Save Flow/Close folder preview buttons, current status and unchanged entered name. Viewport and document scroll width both 655px. This is a fictional preview, not live filesystem evidence.

## Completed local validation

| Scope | Revision | Actual result |
| --- | --- | --- |
| Full Python suite | Runtime/UI `3d20e74b`; original regression assertions | **1707 passed, 2 failed, 10 warnings**, 672.86s. Both failures expected the old separate flow-config JSON / folder inventory. No other failures. |
| Both corrected regression modules (parallel execution and shared layout) | `dea773e6`; execution code unchanged | **53 passed, 11 warnings**, 20.91s. Browser mode/parallelism assertions now inspect embedded FLOW configuration; layout repair covers README/requirements/immutable versions. |
| New handover cases, including isolated Python execution and packaged Outlook helper | Runtime/UI code represented by `3d20e74b` | **11 passed**, 5.57s. Outlook's command runner was synthetic; no Outlook session was opened. |
| Lifecycle plus initial handover cases | Intermediate code with connection fix | **72 passed, 1 warning**, 60.82s. |
| Frontend | Final UI code | **22 Node suites passed**; app.js syntax passed. |
| Preview / diff hygiene | Final UI code | HF-07 walkthrough passed; `git diff --check` passed. |

The full local invocation retained two obsolete test assertions, so it is **not** reported as a clean full-suite pass. The corrected complete modules passed afterward. Final-head CI must provide clean full-suite results before merge. Warnings were the existing Starlette/httpx and TestClient timeout deprecations.

Actual local pytest wrapper (the Python executable was the host's 3.13 ARM64 interpreter; example uses `python` for portability):

```powershell
python -u -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests','-q','--basetemp='+tempfile.gettempdir()+'/metronome-handover-final-'+uuid.uuid4().hex,'--junitxml=test_reports/handover-final.xml']))"
```

The regression retest used the same wrapper with `tests/test_flow_parallel.py` and `tests/test_flow_shared_layout.py`. Ignored local evidence: `test_reports/handover-final.xml`, `handover-final.log`, `handover-regression-modules.log`, and `handover-diagnose.log`. The symlink override affects pytest's optional convenience link only.

## Merge evidence cutoff

Final-head Windows/Linux CI: **PENDING at this committed cutoff**. Before merging, the PR testing section must contain the exact final run URL, tested head SHA, platform counts, warnings/skips and conclusions. That later PR evidence supplements this report; no passing result is claimed here for unfinished CI.

Live HF-08 (work-PC portal/Outlook authentication, second operator, network share, SQL) and HF-09 (Task Scheduler): **NOT RUN**, because no live work-PC execution was performed in this task. Follow the plan after updating. Files contain frozen catalog periods; external dependencies/access are still required. Incomplete recordings produce an explicit draft script, not an executable validated Flow.
