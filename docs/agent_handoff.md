# Agent Handoff

## Current Objective

Finish the live Flows acceptance work in Citrix. First, use the ASAP WiFi flow
as the reference and compare the live Retail `Flagship Experience` report with
a report-specific Metronome scan. Its filter titles must be `Period`,
`Dimension`, and `Measure`, and the visible Measure list must match the portal.
Then rerun the repaired M Tracker flows in headed mode, confirm no run exceeds
30 minutes, and enable the GSCM bookmark flow's managed SQL insertion into the
case-sensitive PostgreSQL target `bi_reporting."GSCM_Test"`. Validate that
target through SELECT-only pgAdmin queries.

## Repo State

- Branch: `main`
- Latest delivered behavior commit: `1c9bdab` (`Cover exact GSCM SQL target casing`)
- `main` includes all behavior changes through `1c9bdab`.
- The repo is private.
- Preserve untracked `governance.db-shm` and `governance.db-wal`.

## Delivered Changes

- `2da8cb6`: wait 60 seconds after GSCM report launch, retry the full flow once
  with a 120-second wait, then fail normally.
- `4de81b7`: preserve the native GSCM XLSX when optional CSV normalization
  fails and transformation and SQL output are disabled.
- `5b7b35b`: use Edge's native download event, validate download completion,
  and prevent any post-download processing error from reopening GSCM or
  re-checking the Public bookmark tab.
- `2d27fc5`: discover ASAP Period and Measure controls and normalize structural
  report-filter labels.
- `b7c66ed`: recognize generated labels formed from multiple hexadecimal runs
  joined by underscores.
- `806bd09`: repair saved ASAP flow references when a report moves between
  portal menu groups, including an already-duplicated catalog state. This is
  the M Tracker stale-path repair.
- `2778c82`: impose a hard 30-minute flow-run limit, record `runtime_limit`,
  and stop the exact assigned headed or headless worker after expiry.
- `1c9bdab`: prove that the requested mixed-case PostgreSQL table name
  `GSCM_Test` remains double-quoted instead of folding to `gscm_test`.
- Every GSCM bookmark scan replaces the previous bookmark snapshot instead of
  retaining stale rows from an older scan.

## Live Citrix Evidence

- ASAP catalog scan: 97 reports total. Retail had 37 catalog rows, including
  21 active and 16 stale. The active report is
  `Retail > Experience > Flagship Experience`.
- GSCM clean bookmark scan: 261 current bookmarks discovered from
  `gds_bookmark`; the stale Private/Public residue was removed.
- Exact bookmark discovered at
  `Public > SCM > Sell-in Biz Plan > SIBP_CI_Series_ASP_Global`.
- GSCM run 200 succeeded in 2m17s and reported `Saved 1 XLSX export(s).` The
  navigation phase took 2m15s and Edge-native file export took 1s.
- Windows Downloads visibly contained
  `GSCM_SIBP_ASP_Global_export (3).xlsx`, 61 KB, created at 4:27 PM.
- On the preceding ASAP report-specific scan, `Measure` was discovered and
  `202623` was absent, but the generated underscore-separated hexadecimal
  label remained. That exact remaining defect is fixed by `b7c66ed`.
- Build `20260821-165304`, containing `b7c66ed`, was installed on the BI
  desktop. The setup restart disconnected the inner RDP session. Reconnection
  reached the expected `CORP\\meto.mx` credential prompt, but Windows rejected
  the saved RDP credential. Do not retry automatically after this authentication
  error.

## Verification

- Full local test suite: 439 passed.
- `main` includes the verified behavior commit `1c9bdab`.
- GSCM end-to-end acceptance: passed.
- ASAP active Retail report discovery: passed.
- ASAP final filter-label acceptance on build `20260821-165304`: blocked only
  by the rejected RDP credential. This is not complete and no completion Gmail
  has been sent.

## Next Step

The current WorkDev Chrome Remote Desktop session is available, but Edge has no
known HyperVM Workdev portal entry. Do not use the visible Samsung VDI portal
as a substitute. Once the trusted HyperVM entry is opened or supplied, connect
to the inner BI desktop and install the latest build. Select ASAP `Flagship
Experience`, run `Scan report`, and compare every Measure option with the live
portal. Browser Find must return zero matches for `202623` and the hexadecimal
prefix `7D4E`. Next, run and monitor the affected M Tracker flows headed. Only
after ASAP and M Tracker pass, edit the existing GSCM bookmark flow to use
Replace all rows with database/schema/table set to the deployed database,
`bi_reporting`, and `GSCM_Test`; run it once and verify the exact quoted target
with SELECT-only pgAdmin queries. Send the promised completion Gmail only after
all live checks pass.
