# Metric Contracts

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

## Impact views last 30d
- Business meaning: Weighted view count used to prioritize alerts by business impact.
- Numerator: Same base rows as `Views last 30d`, with premium viewer rows counted as 5 views and all other rows counted as 1 view.
- Denominator: None.
- Grain: Alert asset.
- Date logic: Same 30-date CSV window as `Views last 30d`.
- Premium viewer matching: Case-insensitive match against the admin-managed premium viewer email list, compared to the resolved `Users.UserId`.
- Source impact: Sum weighted views for distinct reports fed by the degraded source.
- Report impact: Weighted views for the stale or refresh-problem report.
- Alert ordering: Open alerts sort before closed/expected alerts; within that, higher impact sorts before longer problem age.
- Edge cases: If only legacy report/day aggregate usage exists, impact equals raw views because per-viewer premium weighting is unavailable.
