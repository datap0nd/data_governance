# Semantic week ranges and Excel-family downloads: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-09 16:36; final CI will be recorded in the PR testing section
- Tested code revision: uncommitted implementation based on `ed28744e875d48b661575f526035090cbf768f4a`
- Environment: Windows ARM64, Python 3.13.15, Node 24.19.0, Chrome 152.0.7977.83, Playwright 1.62.0
- Overall finding: PASS for the approved fictional journey, focused automated behavior and syntax checks; final-head CI is pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| UI-01 | Opened the generated clickable fictional preview, selected the proposed range, inspected containing-box and cell highlights, changed the start week, and exercised the confirm/recovery action. | Local Codex visualization, 2026-09-09 | PASS; owner replied **yes** to the demonstrated journey. | Approval in the implementation task; fictional data only. |
| RANGE-01–04, COMPAT-01, XLS-01–02 | `python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests/test_recording_ranges.py','tests/test_recording_range_editor.py','tests/test_recording_v2_model.py','tests/test_flow_excel_formats.py','-q','--durations=5','--basetemp='+tempfile.gettempdir()+'/range-excel-focused-final-'+uuid.uuid4().hex,'--junitxml=test_reports/range-excel-focused-final2.xml']))"` | Uncommitted worktree based on `ed28744e`; Windows ARM64 | PASS; 74 passed in 44.65s, 0 failed/skipped/warnings, exit 0. | `test_reports/range-excel-focused-final2.xml` (ignored local evidence). |
| Syntax/model | Python compilation for the six changed modules; `node --check` for the recording model, editor and range preview; `node tests/test_recording_visual_model.mjs`; `git diff --check` | Same worktree/environment | PASS; Python and JavaScript syntax clean, model assertions passed, diff whitespace clean. Git emitted only expected checkout line-ending notices. | Console output in the implementation task. |

## Unperformed or blocked in-scope checks

None at this evidence cutoff. Required final-head CI is pending and belongs in
the PR testing section.

## Findings, limitations and retests

An early combined diagnostic exposed a virtual-calendar race: Playwright's
pre-click scrolling could repaint an `nth()` locator onto a different week. The
runtime now reacquires the logical ISO week and atomically verifies its identity
before dispatching the DOM click, then separately proves selected state. The
virtualized fixture passed individually and in the final focused run. Earlier
pytest diagnostics also encountered Windows error 1463 while pytest cleaned its
convenience symlink; the final command used the repository-documented
`_force_symlink` workaround and exited zero. Synthetic fixtures prove the
contracts and failure behavior without asserting a particular third-party
calendar's markup.

## Merge evidence

Final-head CI is pending. The PR testing section will identify its run URL,
exact tested head SHA and result before merge. The PR merge record will supply
the merge SHA; this report does not claim post-deployment verification.
