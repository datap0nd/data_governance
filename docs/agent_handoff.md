# Agent Handoff

## Current Objective
Integrate Power BI usage CSVs into Metronome so reports, sources, and alerts show raw and weighted usage impact.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit: f95bfc9 Integrate usage impact metrics
- Public repo: yes
- Push status: pending push after this handoff update

## Decisions Made
- `usage_files_path` is the primary source for usage CSVs, with `USAGE_FILES_PATH` and `DG_USAGE_FILES_PATH` as fallbacks.
- Usage import reads `Reports.csv`, `Report_views.csv`, and `Users.csv`; report matching prefers report GUID and falls back to normalized report name.
- The last-30-days window is anchored to the latest date present in `Report_views.csv`.
- `Views last 30d` is raw view count. Alert `Impact` is weighted view count, with premium viewers counted 5x.
- Source usage sums each distinct report once per source, even if the source feeds multiple tables in that report.
- Premium viewer management is local-admin only and does not use a password.
- Alert sorting prioritizes open alerts, then weighted impact, then days in problem state.

## Files Changed
- app/usage.py: CSV import, per-user usage storage, report/source usage maps, premium impact weighting.
- app/routers/usage.py: local-only premium viewer and usage sync endpoints.
- app/routers/reports.py: report `Views last 30d` from shared usage helper.
- app/routers/sources.py: source `Views last 30d` rollup from reports fed by each source.
- app/routers/actions.py: weighted `impact_views_30d` on alerts and impact-first ordering.
- app/routers/scanner.py: `Sync Usage` uses CSV import when configured, then falls back to legacy PowerShell sync.
- app/database.py, app/models.py, app/config.py, app/main.py: schema, API model, config, and router wiring.
- app/static/index.html, app/static/app.js, app/static/style.css: Premium Viewers admin tab, usage columns, alert impact column.
- docs/metric_contracts.md: metric definitions for raw views and weighted impact.

## Commands And Checks
- `git pull --ff-only`: already up to date before edits.
- `PYTHONPYCACHEPREFIX=/private/tmp/dg-pycache python3 -m compileall app`: passed.
- `node --check app/static/app.js`: passed.
- `git diff --check`: passed.
- Usage import smoke test with temporary CSVs and in-memory SQLite: passed.
- `node .../impeccable/scripts/detect.mjs --json --fast app/static`: warnings only, all pre-existing classes of issues (side accent borders, layout transitions, single-font heuristic).
- Not run: full FastAPI server/browser QA, because app startup begins the scheduler and can dispatch due scheduled emails.

## Open Questions
- No blocker. Confirm after deploy that the configured CSV folder contains the expected file names and headers.

## Next Step
On the admin PC, set `usage_files_path`, restart Metronome, open Admin -> Premium Viewers, add premium emails, then run Sync Usage.
