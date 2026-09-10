# Modern Excel open recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 07:08; final CI will be recorded in the PR testing section
- Tested code revision: `4e939ef988ff2732c829e424809086a0042cb7b5`
- Environment: Windows ARM64, Python 3.13.15, openpyxl 3.1.5
- Overall finding: PASS for focused Excel-family behavior and syntax; final-head CI pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| XLS-01–04 (diagnostic run) | Focused `tests/test_flow_excel_formats.py` run with a fresh temporary base. | Working tree based on `6c8dfe59`; Windows ARM64/Python 3.13.15 | FAIL; 37 passed, 4 failed because legacy assertions still expected the intentionally replaced generic modern-reader diagnostic. | `test_reports/modern-excel-open-focused.xml` (ignored local artifact). |
| XLS-01–04 (focused retest) | Same focused pytest command with JUnit `test_reports/modern-excel-open-focused-final.xml` and a fresh temporary base. | `4e939ef988ff2732c829e424809086a0042cb7b5`; Windows ARM64/Python 3.13.15 | PASS; 41 passed in 19.12s, no skips or warnings. | `test_reports/modern-excel-open-focused-final.xml` (ignored local artifact). |
| Syntax | `python -m py_compile app/flow_worker.py tests/test_flow_excel_formats.py` | Same revision/environment | PASS. | Command output. |
| Whitespace | `git diff --check` | Same revision/environment | PASS; only Git's configured LF-to-CRLF notices were emitted. | Command output. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Final-head CI | NOT RUN | PR head is not final yet. | Run required CI on the final PR head and record its URL and SHA in the PR. |

## Findings, limitations and retests

The first run proved the compatibility conversion itself: the generated Strict
OOXML workbook normalized successfully and its original bytes were preserved.
The four failures were expected-message mismatches caused by the intentional
replacement of a generic openpyxl failure with an XLSX container diagnostic.
After updating those expectations, the complete focused file passed. Synthetic
workbooks cannot represent every producer-specific extension, so unknown valid
parser failures retain a safe reader category without including workbook
contents.

## Merge evidence

Pending. Before merging, the PR testing section will record the final CI run,
tested head SHA and result. The PR merge record will supply the merge SHA.
