# Fictional worksheet preview evidence

Observed through the Codex in-app browser on 2026-09-12, approximately 16:28–16:57 UTC, against the uncommitted preview files on baseline `c3f0869adf03e4ead1d497af22a20a1a8115bedd`. File hashes are recorded in `local-checks.json`. This is a static fictional prototype, not evidence of production Flow UI implementation or a database write.

Preview: `/static/recording-preview/excel-worksheets.html`, served from `app` on loopback port 8768. In-app browser tab 3. The browser accessibility snapshots supplied the observations below.

| Procedure | Actual observation |
| --- | --- |
| Enable the checkbox under After download. | Exactly two radio options appeared: **Append named worksheets** and **Load one named worksheet**. The SQL label remained `Replace all rows · reporting.regional_orders`. |
| Choose one named sheet, enter `Totals`, save, and simulate. | `Succeeded`; `17 rows loaded from Totals`. Timeline reported three workbook sheets, the selected sheet's 17 rows and three columns, and a fictional 17-row SQL outcome. |
| Append `North` and `Totals`, save, and simulate. | `Failed · Excel processing`; names and 15-versus-three column counts were shown, with `SQL did not start. The original workbook is preserved.` |
| Select **Choose worksheets** from that failure. | Returned to the expanded After download section with names preserved. The three detected names appeared as selectable controls. |
| Remove `Totals`, add `South`, save, and simulate. | `160 rows loaded from North, South`; per-sheet counts 120 and 40 were displayed. |
| Change the fictional workbook to **Selected sheet renamed**, simulate. | Failure identified missing `North` and listed `North 2027`, `South`, `Totals`; no fictional SQL start. |
| Edit names to `South` and `North 2027`, enable simulated save failure, and save. | Error beside Save: `Could not save the flow. Your worksheet selection is still here. Try saving again.` Both entered names remained. |
| Cancel and reopen settings. | Restored previously saved `North` and `South`. |
| Reset preview; choose **One sheet · 12 data rows**; simulate with option off. | `Succeeded`; `12 rows loaded from Orders`. |
| Choose **Row wider than its header** and simulate. | Error identified `daily_orders.xlsx`, `Orders`, row 9, 16 populated columns versus 15 headers, and no fictional SQL start. |

Earlier in-app tabs 1/2 also showed the default multiple-sheet failure and empty-name validation, but were closed by the UI session before the walkthrough finished. No passing result is claimed for interactions interrupted by a missing tab or selector timeout. The successful observations above were obtained on tab 3.

At the prototype cutoff, owner feedback and the actual application walkthrough were pending.

## Implemented controls — 2026-09-12 17:27–17:31 UTC

The owner then approved this journey with “implement and merge to main.” The following evidence uses the actual `app.js`, `style.css` and `flow_run_log.js`, served with fictional in-memory HTTP data by `tests/test_flow_excel_ui.py`. It is evidence of real application controls with synthetic responses, not a production run or SQL commit.

Revision: `4c9c57dec767afd7fdf3b4ca7e5462d343dd8ab4` plus the UI changes. Verifier source fingerprint and asset/screenshot SHA-256 values are in `local-checks.json`. Automated browser: Chrome 152.0.7977.83, Playwright 1.62.0, at 1440×1000 and 390×844. In-app browser tab 4 independently walked the failure link, selected both detected names, switched to single mode and verified inline rejection without losing either name.

| Procedure | Observed result |
| --- | --- |
| Open a failed run with a preserved partial bundle. | **Excel processing failed**, workbook and all detected names shown, original preserved and SQL not started. **Choose worksheets** present; **Retry SQL only** absent. |
| Follow Choose worksheets. | Correct Flow opened with After download expanded, checkbox enabled and exact detected names available as buttons. SQL remained Replace all rows / FixtureDB.reporting.regional_orders. |
| Select two names, switch to single and save; try empty/duplicate names. | Inline validation identified the invalid choice; no fixture save occurred and entered names remained. |
| Inject 503 and field-specific 422 saves. | Failure visible, Save usable again, selected names preserved; the 422 opened/focused the worksheet field. |
| Save append, reopen, save single, reopen, disable, save. | Exact names and order round-tripped; explicit null restored the default. SQL mode/table remained unchanged. |
| Cancel a changed name and reload. | No persisted change; saved selection restored. Existing builder draft behavior on same-page navigation is preserved. |
| Open portal and local-file settings; change file type to CSV and back. | Default off in both; exact single selection saved. Local CSV hides the Excel controls; changing back restores unsaved names. |
| Walk the 390px layout. | Controls remain readable, names/save accessible, form has no horizontal overflow. |

- [Run error screenshot](evidence/worksheet-error.png)
- [Desktop validation and recovery](evidence/worksheet-settings-desktop.png)
- [Narrow-screen single-sheet setting](evidence/worksheet-settings-mobile.png)
