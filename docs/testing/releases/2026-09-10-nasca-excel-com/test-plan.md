# NASCA Excel-COM Flow recovery: test plan

- Change: open NASCA-wrapped modern Excel Flow downloads with desktop Excel, normalize to CSV, then reuse the existing SQL path.
- Scope: Windows Flow download processing and its pywin32 dependency; recorded navigation is unchanged.
- Risk: high for protected workbook handling and SQL input, contained to a non-ZIP protected payload whose declared filename is modern Excel and whose Flow contract requires tabular validation.

## Prerequisites

- Windows BI desktop with Microsoft Excel, NASCA and the updated Metronome worker running as the signed-in user.
- Existing unchanged five-step `MTracker_subs` recording and its configured SQL destination.
- Synthetic OLE and opaque non-ZIP fixtures plus a fake Excel COM application; no production workbook is copied off the work PC.

## Checks

| ID | Action | Expected result | Evidence |
| --- | --- | --- | --- |
| NASCA-01 | Process both OLE-signature and opaque non-ZIP recorded `.xlsx` files through `_store_completed_download` with a fake Excel COM application. | Under a trusted recorded/scan-selected Excel contract, the original encrypted bytes are preserved; Excel opens read-only with links/events/alerts/macros disabled; UTF-8 CSV is normalized and becomes the SQL-ready artifact. An unrelated corrupt `.xlsx` without that contract still receives the normal container error. | Focused pytest output. |
| NASCA-02 | Run the recorded ASAP stable-staging test and the long-preamble SQL-normalization test. | Recorder navigation still hands off from stable staging; unencrypted text downloads still use the existing path. | Focused pytest output. |
| NASCA-03 | Process ordinary CSV, XLS, XLSX and corrupt/sign-in fixtures through the Flow suite. | Existing format behavior is unchanged; a missing pywin32/Excel installation produces an actionable error and no SQL starts. | Full Python suite and final Actions run. |
| NASCA-04 | On the deployed exact main SHA, open `MTracker_subs`, review its five steps, and run **Test recording** without editing or re-recording. | Download completes, desktop Excel unwraps the NASCA workbook, normalized CSV validation passes, and the recording can be saved normally. | Protected test/run reference, build SHA, terminal status and sanitized timing. |
| NASCA-05 | Run the saved production flow exactly once and monitor through SQL replace/insert completion. | One successful producing run reaches SQL, reports the row/result summary, and retains recovery evidence. | Protected run reference and sanitized status/row summary. |

## Negative and recovery cases

- If Excel/NASCA cannot open the file for the worker account, fail with the desktop-Excel recovery message and do not transform or load SQL.
- If Excel COM is unavailable, rerun `setup.ps1`; the Windows-only pywin32 dependency must be installed. Linux CI must not install it.
- Excel is launched in a separate hidden instance, read-only, with update links, events, alerts and VBA disabled; it is closed on success or failure and its temporary CSV is removed.
- Do not use **Record again** or change the five persisted steps. Do not publish the protected workbook, portal URL, credentials or business rows.
- Do not start a manual portal download during the recording retest; the recorder uses a private UUID staging name and the scan-based watcher must observe only the download initiated by the recorded click.

## Cleanup and rollback

- Preserve the newest failed and successful run evidence under normal retention.
- A rollback removes the COM recovery branch and the Windows pywin32 dependency; it restores the previous explicit rejection but does not alter recordings or SQL configuration.
