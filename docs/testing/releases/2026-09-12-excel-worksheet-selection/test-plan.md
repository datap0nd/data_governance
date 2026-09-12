# Excel worksheet selection: test plan

- Baseline: `c3f0869adf03e4ead1d497af22a20a1a8115bedd`.
- Related [report](test-report.md) and [browser evidence](browser-evidence.md).
- Draft scope: backend worksheet selection and a fictional clickable preview. The production Flow settings controls await owner feedback required by `DESIGN.md`.
- Environments: isolated Python 3.13 fixtures; synthetic Chromium downloads; in-app browser preview. SQL calls in synthetic browser tests are captured, with no database connection.

## Prerequisites and data

Use the checkout-owned `.venv` created by `tools/check.ps1 -Mode Setup`.
Keep test schedules manual. Update the application and its workers together when installing the completed change: workers advertise `excel_worksheets_v1` before claiming jobs with the new named-sheet setting.

Create fictional workbooks with these sheets: `North` and `South` have matching `Code, Units` columns and duplicate/small data sets; `Totals` has a different schema and 17 rows. Also prepare a single-sheet workbook, a renamed sheet, an empty sheet, reordered columns, and a data row wider than its header. Never use production reports as test fixtures.

## Cases

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| XL-01 | Leave worksheet selection off; process a workbook containing one worksheet. | Existing header/row processing and SQL mode continue. Retain duplicates and all data rows, even fewer than 100 or with `Total` in the header. Capture CSV and per-sheet row count. |
| XL-02 | Leave selection off; process a workbook with two or more worksheets, including an empty or hidden second worksheet. | Fail with `This Excel has more than one sheet. Please enable the option in Flows.` Include file and available worksheet names. Original workbook retained; no completed CSV or SQL call. Download-only Excel flows also disclose multiple sheets. |
| XL-03 | Enable selection; choose **Append named worksheets**; enter `South` then `North`, one name per line. | Append in the supplied order with one header. All rows, including duplicates, reach the captured SQL input. No implicit inclusion of other sheets. |
| XL-04 | Choose **Load one named worksheet**; enter `Totals`. | Load precisely its 17 rows and its schema. Size and words in its name/header do not affect inclusion. |
| XL-05 | Append sheets whose column count, normalized column names, or column order differs. | Fail with the workbook, both sheet names, expected/found columns, and guidance to correct the workbook or named selection. Do not union schemas, fall back to raw output, or start SQL. |
| XL-06 | Rename a selected worksheet; retry the same saved configuration. Also try different letter case. | Fail naming the missing selection and available names. Do not choose another sheet automatically. |
| XL-07 | Submit empty names, duplicate names, two names in single mode, or one name in append mode. | Reject configuration beside the worksheet field. Preserve entered values. API error location identifies `excel_worksheets`. |
| XL-08 | Select a sheet with no usable header, no required data, or data beyond the header width. | Fail during Excel processing, identify file/sheet and row where available, retain original/partial evidence, and make no SQL call. |
| XL-09 | Save a choice, reload it, update through an older client omitting the new field, then explicitly set it to `null`. | Choices survive omission and are frozen into jobs/scripts. Explicit null restores one-workbook/one-sheet behavior. Existing exact local-file selections remain valid. |
| XL-10 | Queue named-sheet processing to a worker lacking the capability; register a compatible worker. | Old worker cannot silently ignore the choice. Compatible worker receives the exact frozen configuration. |
| XL-11 | Exercise recorded, catalog, generic portal and local-file processing; use synthetic desktop Excel objects for NASCA and real fixture readers for XLS/XLSB/OOXML. | Same selection and mismatch rules. Existing CSV, preamble, trimming, original-copy and COM cleanup behavior remains covered. An explicit named selection cannot silently process non-workbook bytes. |
| XL-12 | In preview, trigger failure, use **Choose worksheets**, correct names, save, and simulate again. Simulate failed save and Cancel. | Clear recovery action; visible error beside Save; failed save preserves edits; Cancel restores saved values. |

## Commands

The exact executed selectors, syntax paths, source fingerprints and outcomes are preserved in [local-checks.json](local-checks.json). Reproduce an entry using:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath @('<selectors from the entry>') -SyntaxPath @('<syntax paths from the entry>')
```

For browser cases on this Windows verifier, select one database-backed case per invocation: its shared `DG_FLOWS_ROOT` otherwise causes repeated fixture IDs to collide. Run portable-script tests sequentially; their host-wide execution locks intentionally reject concurrency. Required final-head **Merge ready** CI remains the authoritative full Python regression.

For the clickable fictional preview:

```powershell
.\.venv\Scripts\python.exe -m http.server 8768 --bind 127.0.0.1 --directory app
```

Open `http://127.0.0.1:8768/static/recording-preview/excel-worksheets.html`.
The page makes no application API requests; its Save and Run controls operate only on fictional data in that tab.

## Acceptance and cleanup

Before merge, obtain owner feedback on the preview, implement and walk the actual Flow controls, complete the affected UI checks, and record the passing final-head CI URL and SHA in the PR. The draft backend alone must not be merged: users need the configuration control when a multi-sheet workbook fails.

The verifier owns its isolated test directories. Close the preview server when review is finished. Keep the original downloaded workbook and failed partial output for diagnosis; do not delete or modify owner reports. Restore test-only configuration if testing in a disposable app instance. SQL append/replace semantics and schedules are outside this change.
