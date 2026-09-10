# NASCA direct pywin32 cell reads: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-10T22:23:00Z.
- Tested source: 6062b6098a43d3c344b2bfe96f4d544062b0b9c6 plus this
  uncommitted implementation/test diff.
- Environment: Windows, checkout-owned Python 3.13.15 and locked dependencies.
  Excel tests use synthetic COM fixtures.

## Executed evidence

| Case | Procedure | Actual result | Evidence |
| --- | --- | --- | --- |
| C-01 through C-04 | Exact focused Verify command in the plan, with both syntax targets. | PASS: 6 cases in 2.87s; syntax passed; no skips/warnings. | .test-runs/20260910T222204246Z-20324-896ae4d6/result.json |
| Live baseline subsidiary | Test 163, flow 5, unchanged recording 46, installed 6062b6098, Chrome 152.0.7977.83. | FAIL: file recognition now reaches Excel, but the old open/export operation fails. No SQL load. | LIVE-THREE-FLOWS-20260911-SUBS-163 |
| Live baseline country | Test 164, flow 6, unchanged recording 47, installed 6062b6098. | FAIL at the old Excel open/export operation. No SQL load. | LIVE-THREE-FLOWS-20260911-COUNTRY-164 |
| Independent source check | Download Main and Country through the existing normal browser and open each from its Downloads UI. | Both fresh files produce Excel's invalid format/extension message. The older Main workbook remains readable and untouched. This does not test pywin32 and does not establish that NASCA access through COM is impossible. | LIVE-THREE-FLOWS-20260911-NATIVE-EXCEL |

The owner clarified that protected files are read with pywin32 and that the
download is the source of truth. Code comparison found that the legacy scanner
reads cell values, whereas the Flow reader opened a hard-link alias and used
Excel SaveAs. This change aligns the data-access method without adopting the
legacy reader's unsafe quit of a borrowed Excel session or its per-cell call
cost. It also preserves the existing application-event setting instead of
disabling all integration events. Workbook VBA remains disabled.

One bounded review covered source-path identity, bounded reads, value
preservation, no workbook save, open/read failure, settings restoration and
failure before publication. No UI journey changed. Documentation was checked
against those behaviors and the actual evidence.

## Pending after cutoff

Required final-head CI is pending. Record its run URL and exact head in the PR
before merging. The revised pywin32 implementation has not yet run on the work
PC. Neither MTracker production flow is claimed fixed or succeeded. Append
dated, revision-specific live results after installation; retain prior failures.
