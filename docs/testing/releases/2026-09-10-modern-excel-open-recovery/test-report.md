# Modern Excel open recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10; final CI will be recorded in the PR testing section
- Tested code revision: uncommitted implementation based on `6c8dfe594904442f65d51ff7410bbb8be5e2ffae`
- Environment: Windows ARM64, Python 3.13, openpyxl 3.1.5
- Overall finding: implementation verification in progress; final-head CI pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| XLS-01–04 (diagnostic run) | `python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests/test_flow_excel_formats.py','-q','--durations=5','--junitxml=test_reports/modern-excel-open-focused.xml','--basetemp='+tempfile.gettempdir()+'/modern-excel-open-'+uuid.uuid4().hex]))"` | Uncommitted implementation, Windows ARM64/Python 3.13 | FAIL; 37 passed, 4 failed. The new Strict OOXML case passed; four pre-change assertions still expected the superseded generic modern-reader message. | `test_reports/modern-excel-open-focused.xml` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| XLS-01–04 final focused run | NOT RUN | Diagnostic failures required assertion updates. | Rerun the focused file after updating the expected structural diagnostic. |
| Final-head CI | NOT RUN | PR head is not final yet. | Run required CI on the final PR head and record its URL and SHA in the PR. |

## Findings, limitations and retests

The first run proved the compatibility conversion itself: the generated Strict
OOXML workbook normalized successfully and its original bytes were preserved.
The four failures were expected-message mismatches caused by the intentional
replacement of a generic openpyxl failure with an XLSX container diagnostic.
Synthetic workbooks cannot represent every producer-specific extension, so
unknown valid parser failures retain a safe reader category without including
workbook contents.

## Merge evidence

Pending. Before merging, the PR testing section will record the final CI run,
tested head SHA and result. The PR merge record will supply the merge SHA.
