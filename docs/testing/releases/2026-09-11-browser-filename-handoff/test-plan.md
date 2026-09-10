# Browser filename handoff: test plan

- Baseline: `7dbf0e5be05a9d98d2d53b205179d06d82663b07`.
- Scope: preserve the browser response filename as format-routing metadata for recorded downloads whose protected staging path must stay untouched.
- Report: [test-report.md](test-report.md).
- Environments: checkout-owned Windows/Python 3.13; synthetic Chromium downloads and Excel stub; owner-requested BI desktop runs for Inflow and both MTracker flows. Outflow is excluded.

## Prerequisites and cases

Use `tools/check.ps1 -Mode Setup` once. Fixtures contain fictional data only. Keep live workbooks, URLs, credentials and raw logs outside Git; identify protected evidence with opaque references.

| ID | Procedure | Expected result and evidence |
| --- | --- | --- |
| F-01 | Run the NASCA recording-validation selector below for both OLE and opaque wrapper parameters. | Browser downloads to an extensionless path; Excel receives that exact path; normalized CSV has one fixture row; validation executes no SQL. |
| F-02 | Run the Excel-named text selector. | Text content wins over the response extension; normalized two-row CSV reaches the SQL stub. |
| F-03 | Run the three `test_flow_sql.py` selectors below. | Excel failure publishes no output, preserves source bytes; CSV, HTML and PDF content retain their formats; executable Excel add-ins are rejected. |
| L-01 | On the installed merge revision, Edit each MTracker flow, Review recording, Test recording. Preserve existing steps and destinations. After each passes, Back to Edit Flow and Save changes. | Recording validation succeeds on the installed core. Record app/worker revision, browser version, test ID, result and sanitized error if any. A failure must not activate the draft or write SQL. |
| L-02 | Run Inflow, MTracker_subs and MTracker_country once each using their saved destinations. Inspect Run history and SQL-stage results. | Each production run succeeds with nonempty normalized output and a confirmed SQL commit. Record run ID, revision and row count. Reconcile any unknown SQL outcome before retrying. |
| L-03 | In pgAdmin use SELECT-only queries for destination metadata, row counts, period bounds, key nulls and duplicate-grain checks based on actual columns. Compare with run artifact counts and report selection. | Destination identity, structure, periods and counts agree with the exported report; investigate unexpected nulls/duplicates. Keep business values protected. |

## Focused local commands

Run the browser selectors separately because the verifier's explicit managed Flow root is shared within a process:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath 'tests/test_recording_optional_checks.py::test_recording_validation_normalizes_nasca_input_for_configured_sql[ole-wrapper]' -SyntaxPath app/flow_worker.py,app/flow_recording_runtime.py,tests/test_recording_optional_checks.py,tests/test_flow_sql.py
.\tools\check.ps1 -Mode Verify -TestPath 'tests/test_recording_optional_checks.py::test_recording_validation_normalizes_nasca_input_for_configured_sql[opaque-extensionless-wrapper]'
.\tools\check.ps1 -Mode Verify -TestPath tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_extensionless_nasca_failure_does_not_publish_or_load_binary,tests/test_flow_sql.py::test_browser_excel_filename_hint_does_not_override_known_content,tests/test_flow_sql.py::test_browser_filename_hint_rejects_executable_excel_addins
```

Required final-head `Merge ready` supplies the full Python regression. Do not repeat the full suite locally.

## Acceptance and cleanup

Require focused local evidence and final-head CI before merging. Live completion requires all three production successes plus database validation; merging alone is not deployment evidence. Preserve earlier failed attempts. No scheduling or security changes are planned. Test folders are verifier-owned; live outputs follow the existing retention policy. Do not remove recovery artifacts or manually alter database rows. If the fix fails, retain the failed draft and diagnose before activating or rerunning it.
