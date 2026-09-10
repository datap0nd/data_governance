# Recorded download processing regression: test plan

- Change/PR: restore content-driven normalization and SQL handoff after a recorded browser download
- Code baseline: `7291a559bd160a2f8ecabba5e15817da370b306b`; final merged behavior must use the tested PR head
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11 with Python 3.12, Playwright/Chrome, and generated fictional download payloads; one live ASAP work-PC recovery check remains separate

## Prerequisites and test data

Create isolated downloads containing fictional `Region` and `Units` columns.
Serve UTF-8, UTF-16 LE, and UTF-16 BE tabular responses under the suggested
filename `MTracker_subs.xlsx`; also generate a real OOXML workbook, a damaged
OOXML package, and an Excel-named HTML table. Use pytest temporary directories
and a localhost HTTP server. The SQL integration case replaces the database
write with a capturing test double so no external database is changed.

For the live recovery check, update the BI desktop to the merged `main`, keep
the existing recording and SQL settings unchanged, and rerun the previously
failing MTracker Flow. Do not publish its workbook, SQL data, URL, or browser
state as GitHub evidence.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| HIST-01 | Compare the scan-selected post-download path and recorded runtime at baseline; inspect the `.xlsx` suffix fallback introduced by commit `85fd024` and the recorder's hard-coded ASAP type. | The regression is attributable to recorded capture inventing an ASAP semantic type plus filename-first classification; scan-selected behavior and SQL loading themselves were not reverted. | Reviewed diff/commit IDs in the PR. |
| REC-SQL-01 | In headless Chrome, record a download whose server filename is `1.xlsx` but whose bytes are UTF-16 LE tabular text; enable SQL handoff and capture the artifacts supplied to the SQL loader. | The browser capture completes, content is detected as delimited text, a two-row UTF-8 CSV is supplied to SQL, and no XLSX ZIP error occurs. | Focused pytest output. |
| DET-01 | Send UTF-8, UTF-16 LE, and UTF-16 BE tabular payloads named `MTracker_subs.xlsx` through `_store_completed_download` with recorded output and table processing enabled. | All three are classified from content, normalized to CSV, and report the expected columns and two data rows. | Focused pytest output. |
| HTML-01 | Send a valid two-row HTML table named `MTracker_subs.xlsx` through the recorded Excel processing path without an invented ASAP type. | The compatible HTML table is preserved as an original `.xls` artifact and normalized to a one-row CSV. | Focused pytest output. |
| XLS-REG-01 | Run the Excel-family and Flow SQL suites, including real OOXML, Strict OOXML, prefixed OOXML, legacy/binary workbooks, malformed packages, encoding and SQL artifact regressions. | Valid containers still normalize; malformed/binary content still fails closed; existing scan-selected semantic checks remain intact. | Focused pytest output and final CI. |
| LIVE-01 | On the work PC after updating to merged `main`, rerun the same saved MTracker recording and inspect its result and SQL target without changing or rerecording the Flow. | The existing recording downloads once, produces a normalized artifact, completes SQL insertion, and no modern-workbook ZIP/open error appears. | Sanitized run ID and protected evidence reference. |

## Automated checks

Use the isolated task virtual environment built from `requirements-ci.txt`:

```powershell
$py = Join-Path $env:TEMP 'codex-datap0nd-regression-venv\Scripts\python.exe'
& $py -m pytest tests/test_recorded_output_storage.py tests/test_flow_excel_formats.py tests/test_flow_sql.py -q
& $py -m pytest tests/test_recording_optional_checks.py -q
& $py -m pytest tests -q --durations=20
& $py -m py_compile app/flow_worker.py app/flow_recording_runtime.py tests/test_recorded_output_storage.py tests/test_recording_optional_checks.py
git diff --check
```

The final PR-head GitHub Actions run is authoritative for the complete Linux
Python regression and unchanged frontend checks.

## Acceptance and cleanup

Accept when REC-SQL-01, DET-01, HTML-01, XLS-REG-01, syntax, whitespace, and
required final-head CI pass and the PR is merged to `main`. LIVE-01 may remain
NOT RUN until the BI desktop updates; it is not replaced by synthetic browser
evidence. Remove only task-owned temporary test directories/virtual
environments when no longer needed. Reverting the PR restores the prior strict
filename assertion but also restores this recorded SQL failure.
