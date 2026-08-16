# Metric Contracts

## ASAP FOTA managed snapshot
- Business meaning: Weekly FOTA value at the complete Sell-out geography, operator, and product grain exported by the Regional FOTA report.
- Numerator: Each live flat export week value, renamed to `FOTA Value` by the
  external transform. Multi-week exports may expose compact `YYYYWW`,
  `YYYY-Www`, `Week ww`, or `Www` columns; all are unpivoted into one row per
  source row and ISO week. The live multi-week XLSX matrix may expose only the
  lower `Metrics` header even though its data rows contain one value cell per
  selected week. The normalizer recovers those week columns from the validated
  artifact range only when the physical value-column count matches one-to-one;
  it never truncates populated cells. Blank week cells are not emitted. The
  `Metric`/`Metrics` aliases remain supported directly only for a single-week
  file whose filename supplies exactly one week.
- Denominator: None.
- Grain: Sell-out Region, Sell-out Subsidiary, Sell-out Country, Country Code, Operator, Province, Latitude, Longitude, Category, Biz Sub, Series, MKT Name, Item, and Week.
- Date logic: A complete managed snapshot from 2026-W20 through the latest week
  available in ASAP. Downloads may contain multiple contiguous ISO weeks. The
  transform validates their week columns against the filename, derives Sunday
  `Week Start Date` and Saturday `Week End Date`, and emits long-form rows.
- Filters: ASAP Data Option `Show All`, Category `Weekly`, export view `Export
  Wizard (Sell-out Sub)`, and the 13 contracted dimensions above. The live flat
  export omits the filtered Category field, so the transform adds
  `Category = Weekly` and removes the operational `Metronome Export View`
  lineage column before SQL.
- Geolocation: For each unique valid `Latitude`/`Longitude` pair, the external
  transform uses the offline `reverse_geocoder` GeoNames index. It adds
  `Country` from the returned ISO alpha-2 code, `City` from `name`, `District`
  from `admin1`, and `State` from `admin2`, matching the authorized legacy
  script. Missing, nonnumeric, nonfinite, or out-of-range coordinates retain the
  source row with those four derived fields blank.
- Refresh behavior: Build all transformed files first, then atomically replace `meto_db.bi_reporting.ASAP_Fota`. Never append a rolling 12-week slice and never modify the existing table if any download, transform, or SQL stage fails.
- Validation method: Require every distinct week from 2026-W20 through the live
  latest week, the exact minimum and maximum week, a positive total row count,
  and the full 21-column contract after the live SQL commit.
- Edge cases: Reject ambiguous week-only headers, a filename/week mismatch,
  duplicate week columns, multiple `Metric` columns, missing contracted
  dimensions other than the fixed Category field, unexpected columns, unusable
  headers, an unprovable multi-week matrix width, populated cells beyond a
  resolved header, empty data, a partial requested range, or unavailable
  geolocation dependencies.

## Views last 30d
- Business meaning: Raw Power BI report view count over the most recent 30 dates available in the usage CSV export.
- Numerator: Count of report-view rows from `Report_views.csv`.
- Denominator: None.
- Grain: Report per day per viewer for storage; report-level and source-level totals for display.
- Date logic: Anchor on the maximum `Date` present in `Report_views.csv`; include that date and the prior 29 days.
- Report matching: Prefer `Report_views.ReportId` to `Reports.ReportGuid`; fall back to normalized report name matching.
- Viewer matching: Prefer `Report_views.UserKey` joined to `Users.UserKey`, using `Users.UserId`; fall back to `Report_views.UserId`.
- Source rollup: Sum each distinct report once per source, even when the source feeds multiple tables in that report.
- Premium viewers: Not applied. This metric is raw views.
- Edge cases: Unmatched reports are stored with no local `report_id` and do not appear in local report/source totals until names or IDs can be matched.

## Views in alert prioritization
- Business meaning: Usage-based view value used to prioritize alerts by business impact. Product surfaces label this value `Views` to keep alert triage plain and compact.
- Numerator: Same base rows as `Views last 30d`, with premium viewer rows counted as 5 views and all other rows counted as 1 view.
- Denominator: None.
- Grain: Alert asset.
- Date logic: Same 30-date CSV window as `Views last 30d`.
- Premium viewer matching: Case-insensitive match against the managed premium viewer email list, compared to the resolved `Users.UserId`.
- Source impact: Sum the prioritization view value for distinct reports fed by the degraded source.
- Report impact: Prioritization view value for the stale or refresh-problem report.
- Alert ordering: Open alerts sort before closed/expected alerts; within that, higher impact sorts before longer problem age.
- Edge cases: If only legacy report/day aggregate usage exists, impact equals raw views because per-viewer premium weighting is unavailable.

## Fix This First rank
- Business meaning: Deterministic triage rank for the dashboard's top alerts.
- Numerator: Composite score on each visible action.
- Denominator: None.
- Grain: Action after archive/status filtering and asset deduplication.
- Formula: `impact_views_30d * 1000 + issue_type_weight + min(asset_days, 30) * 25 + status_weight + ownership_weight + affected_report_weight + stale_gap_weight`.
- Issue type weights: refresh failed 900; schedule mismatch 820; source freshness/error 760; refresh overdue 680; task/script failed 620; broken reference 540; changed query 420; default 400.
- Status weights: open 300; investigating 160; acknowledged 90; default active status 120. Resolved and expected actions are not ranked.
- Ownership weight: unassigned actions add 75.
- Affected report weight: source actions add 20 per affected report, capped at 10 reports.
- Stale gap weight: schedule mismatch actions add 5 per hour of worst source/report gap, capped at 72 hours.
- Reason display: The API returns short `triage_reasons` strings so users can see why an item appears in the panel.

## Actions per user last 7d
- Business meaning: Dashboard accountability metric showing who changed or configured the governance tool recently.
- Numerator: Count of `event_log` rows created in the last seven days, grouped by resolved actor name.
- Denominator: None.
- Grain: Actor.
- Date logic: Uses SQLite `datetime('now', '-7 days')` against `event_log.created_at`.
- Actor logic: Uses `event_log.actor`, which is resolved from the request identity middleware's IP/client-key registration. Null or blank actors are grouped as `Unregistered`.
- Filters: Excludes scheduler and system actors so automated email dispatches do not rank as user activity.
- Ordering: Sort by action count descending, then most recent action descending, then actor name ascending.
- Edge cases: Historical rows before actor tracking appear under `Unregistered` if they fall inside the seven-day window.

## Source rows
- Business meaning: Data-row count observed for a source during the latest probe, used to spot empty files or tables.
- Numerator: Count of data rows in the probed source. CSV/TXT and XLSX-family files exclude one header row per file or worksheet. PostgreSQL sources use `COUNT(*)`.
- Denominator: None.
- Grain: Source per probe; source and lineage views show only the latest probe value.
- Date logic: Updates when the source probe runs. Existing historical probes without `row_count` show unknown until re-probed.
- Alert logic: Create a critical `empty_source` action and alert when the latest probe records 0 rows and the previous non-null row count for that source was greater than 1.
- Filters: Source views hide archived sources by default, matching the rest of the source inventory.
- Auto-resolution: Resolve open `empty_source` actions and matching alerts when a later probe records a positive row count.
- Edge cases: Unsupported file formats, inaccessible files, and non-PostgreSQL database sources without direct connection support show unknown. Header-only files display 0 rows. A transition from 1 row to 0 rows does not alert.
