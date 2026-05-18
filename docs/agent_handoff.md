# Agent Handoff

## Current Objective
Ship per-profile email scheduling and align lineage source details with the source freshness rule editor.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit: 5404584
- Public repo: yes
- Push status: pending for current changes

## Decisions Made
- Email schedules are now keyed per BI profile, with selectable content types for open tasks, alerts, or both.
- Profile schedules support daily or week-days-only recurrence plus a send time.
- Lineage source details reuse the same freshness editor behavior as the Sources detail pane.
- Lineage Last Refreshed displays as `YYYY-MM-DD-HH-mm`, while the raw timestamp remains available as hover text.

## Files Changed
- app/database.py: added per-person email schedule fields and indexes.
- app/models.py: exposed person and content-type schedule fields.
- app/routers/email_schedules.py: added per-profile schedule endpoints and dispatcher support.
- app/static/app.js: added per-profile Schedule dialog, reused freshness editor helpers, and compact lineage timestamp formatting.
- app/static/style.css: added row action/status and schedule dialog styling.

## Commands And Checks
- `node --check app/static/app.js`: passed.
- `python3.12 -m compileall app/database.py app/models.py app/routers/email_schedules.py`: passed.
- FastAPI TestClient schedule endpoint smoke test: passed.
- `git diff --check`: passed.
- Browser smoke test on a temporary local server: passed for Email Schedule dialog and Lineage freshness controls.

## Open Questions
- None blocking.

## Next Step
Commit and push these changes to `origin/main`.
