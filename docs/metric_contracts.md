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
