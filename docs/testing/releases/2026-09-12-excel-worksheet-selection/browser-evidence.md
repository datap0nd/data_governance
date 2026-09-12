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

Owner feedback is pending. Responsive verification and a walkthrough of the actual application controls will be recorded after the reviewed journey is implemented.
