# Agent Handoff

## Current Objective

Have the user install the build containing `2143650`; do not access Citrix or
the work PC. Then have the user rerun the affected M Tracker flow headed. The
ASAP site-level Quick scan and M Tracker row refresh are needed only if the
saved report still points to `Advanced > Z8 Command Center > M Tracker` rather
than `Advanced > AI Insights > M Tracker`. Also rerun the GSCM bookmark flow
that produced the August 24 Setting/Public errors. Only after both portals pass,
enable the GSCM flow's managed SQL insertion into the case-sensitive PostgreSQL
target `bi_reporting."GSCM_Test"` and validate it with SELECT-only pgAdmin
queries.

## Repo State

- Branch: `main`
- Latest delivered behavior commit: `2143650` (`Finish M Tracker dashboard workbook runs`)
- `main` includes all behavior changes through `2143650`.
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
- `6b25d8d`: make M Tracker/HTML dashboard downloads trust Edge's completed
  native Download object (including popup races); promote Nexacro caption
  children to their owning controls; fall back to native Nexacro click events;
  perform a real scope rebind; deduplicate gear attempts; and reload the GSCM
  portal component tree before export attempt two.
- `82b2d7f`: correct the M Tracker portion of `6b25d8d`. Edge's native event is
  only proof that an HTML-dashboard download started; completion now comes from
  the stable worker-staging file, so a missing terminal browser event cannot
  strand the run in `download.failure()`. GSCM keeps its separate native path.
- `72db70d`: do not synchronously query or close M Tracker's half-detached
  download popup after staging completes. Return the staged file immediately so
  the existing validated staging-to-target copy can save it in the configured
  flow folder, matching regular ASAP's no-close implementation.
- `2143650`: finish download-only HTML-dashboard workbooks directly from the
  locally validated XLSX instead of silently streaming the completed workbook
  into an unused normalized CSV. The worker validates the XLSX ZIP members and
  CRCs before target creation, checksums it while copying, reports the actual
  detected format, retains normalization when transformation or SQL needs CSV,
  and never reopens the portal after a post-download processing failure.
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
- On August 24, the supplied Metronome run screen showed a current GSCM export
  failing before download: one attempt left the Public bookmark grid empty and
  the other could not open Setting > Favorite even though the live inventory
  contained the exact `btn_setting` component. The same attachment contains no
  M Tracker-specific expanded log; M Tracker's reported failure is separate.
- After installing `6b25d8d`, the user confirmed that M Tracker wrote the file
  but Edge never reported the terminal download state. Source review found the
  worker blocked in unbounded `download.failure()` after the start event. No
  Citrix access was used for this diagnosis or fix.
- After installing the staging fix, the user confirmed that Edge still
  downloaded correctly but the configured flow folder remained empty. The
  remaining pre-store block was the helper's synchronous `popup.close()` in its
  `finally`: Python executed that call before returning the staged path, so
  `_store_completed_download` was never reached.
- After installing the target-copy fix, the user confirmed that the workbook
  appeared in the configured folder but the run remained at `Downloading 1 of
  1 from the dashboard`. The remaining work was an unconditional, unreported
  XLSX-to-CSV normalization and metadata scan after the raw target copy. No
  downstream consumer needed that CSV for this download-only M Tracker flow.

## Verification

- Full local test suite after `2143650`: 463 passed.
- Focused file-processing and HTML-dashboard suite: 148 passed.
- `main` includes the verified behavior commit `2143650`.
- Previous GSCM run 200 passed, but post-`72db70d` live acceptance is pending
  because the August 24 run exposed a newer activation/state failure.
- ASAP active Retail report discovery: passed.
- ASAP final filter-label acceptance on build `20260821-165304`: blocked only
  by the rejected RDP credential. This is not complete and no completion Gmail
  has been sent.

## Next Step

Do not use Citrix; the user will install and validate the latest build. Rerun
the M Tracker flow headed. If its catalog row still has the old menu path, run
ASAP's site-level Quick scan, verify the active row moved under
`Advanced > AI Insights`, refresh that row, and retry. Rerun the affected GSCM
bookmark flow headed and confirm Setting > Favorite, Public-grid binding,
report launch, and native file completion. Also finish the `Flagship Experience`
Measure comparison (`202623` and hexadecimal prefix `7D4E` must be absent).
Only after ASAP, M Tracker, and GSCM pass, configure Replace all rows for the
deployed database, `bi_reporting`, and `GSCM_Test`; run once and verify the
exact quoted target with SELECT-only pgAdmin queries. Send the promised
completion Gmail only after all live checks pass.
