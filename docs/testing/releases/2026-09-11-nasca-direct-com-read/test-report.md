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

## 2026-09-10T22:34Z Inflow scope extension

The owner specified 2025-W01 through now. Live inspection on 6062b6098 showed
Sell-out Week as a generic multi-select and the flow using no period selection.
Code review found that native controls were classified generically and later
semantic discovery merged options without upgrading the type. ISO-week members
and their week label now retain week semantics, including after later generic
observations. This enables the existing start-to-latest period controls.

The exact additional Verify command in the plan passed: 2 cases in 0.63s, both
syntax targets passed, no skips/warnings. Evidence:
.test-runs/20260910T223306636Z-32904-766633a7/result.json, based on commit
5ade0662aecbb00641fe28e09a40ffe446d54945 plus the discovery/test diff.
The six earlier COM cases are unchanged; this new evidence covers only the
additional discovery behavior. A bounded review of this added diff checked
semantic precedence, option retention and non-week controls.

Inflow still requires a new production run with the requested full range.
Country's SQL owner and schema USAGE/CREATE checks passed. The subsidiary
table is currently owned by metomx, while its configured owner rafael lacks
USAGE and CREATE in its destination schema. The owner was asked to choose
between the exact required schema grant and retaining the existing SQL owner;
no permission or ownership changes have been made.
# 2026-09-10 22:41 UTC: shared discovery scope

The owner requested reusable range handling for other reports. Discovery now
recognizes portal-authored week/period labels with valid ISO-week members and
retains those labels; the old Sell-out Week-only fallback is removed. A brittle
source-string assertion was replaced with behavioral discovery coverage.

The test plan's updated discovery command passed 7 cases in 0.61s, with syntax
checks for app/flow_worker.py, tests/test_flow_worker_discovery.py and
tests/test_flows.py. Environment remains checkout Python 3.13.15; no skips or
warnings. Evidence: .test-runs/20260910T224053012Z-29976-536ff1b5/result.json,
working tree based on 3bef7fda68f5a24f21467771580b9901fb93f678. This dated
addition's cutoff is before its commit, final-head CI or live installation.

The owner also approved the requested schema privilege action but redirected
MTracker_subs to meto_db.bi_staging. Existing read-only evidence already showed
Rafael has USAGE/CREATE on that schema. No grant or destination change has yet
been executed at this cutoff.
