# Modern Excel open recovery: test plan

- Change/PR: recover valid Strict OOXML downloads and make malformed modern-workbook diagnostics structural
- Code baseline: `6c8dfe594904442f65d51ff7410bbb8be5e2ffae`; final merged behavior must use the tested PR head
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows ARM64 with Python 3.13 and synthetic Excel-family fixtures

## Prerequisites and test data

Use generated workbooks containing fictional `Code` and `Units` columns. Build
the Strict OOXML fixture by translating the package identifiers in a generated
Transitional workbook. Keep the fixture inside pytest's temporary directory so
neither workbook is retained after the process exits.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| XLS-01 | Process generated `.xlsx`, `.xlsm`, `.xltx` and `.xltm` Transitional workbooks. | Each compatible extension is preserved and each workbook normalizes to the expected CSV rows and columns. | Focused pytest/JUnit. |
| XLS-02 | Process a generated Strict OOXML `.xlsx` containing one fictional data row. | Metronome recognizes the Strict workbook, reads it through an ephemeral compatibility copy, preserves the original downloaded bytes, and produces the expected CSV metadata. | Focused pytest/JUnit. |
| XLS-03 | Process OOXML with leading transport bytes and legacy `.xls`/`.xlt` plus `.xlsb` fixtures. | Existing Excel-family handling remains unchanged. | Focused pytest/JUnit. |
| XLS-04 | Supply opaque content with each Excel-family suffix, a damaged OOXML ZIP, an Excel-named sign-in page and executable add-in extensions. | Invalid modern files report a failed XLSX container rather than an ambiguous reader failure; the other inputs retain precise rejection messages and no VBA is executed. | Focused pytest/JUnit. |

## Automated checks

Run `tests/test_flow_excel_formats.py` once as the smallest affected Python
set, using the Windows pytest symlink workaround and a JUnit file under ignored
`test_reports/`. Run `python -m py_compile` for the edited Python modules and
`git diff --check`. Required final-head CI remains authoritative for the full
Python regression.

## Acceptance and cleanup

Accept when the focused Excel-family set and syntax checks pass, required
final-head CI passes, and the tested PR is merged to `main`. The raw download
must remain unchanged; only the temporary Strict compatibility package may be
discarded when its workbook closes. Revert the merged PR to roll back the
compatibility reader.
