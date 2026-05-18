# Agent Handoff

## Current Objective
Ship per-profile email scheduling and keep existing SQLite databases able to migrate cleanly.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit: e0e753f
- Public repo: yes
- Push status: pushed to origin/main

## Decisions Made
- Email schedules are now keyed per BI profile, with selectable content types for open tasks, alerts, or both.
- Profile schedules support daily or week-days-only recurrence plus a send time.
- Lineage source details reuse the same freshness editor behavior as the Sources detail pane.
- Lineage Last Refreshed displays as `YYYY-MM-DD-HH-mm`, while the raw timestamp remains available as hover text.
- New indexes that depend on migrated columns must live in migrations, not the base schema block, because `init_db()` executes the base schema before migrations on existing databases.

## Files Changed
- app/database.py: added per-person email schedule fields and indexes.
- app/models.py: exposed person and content-type schedule fields.
- app/routers/email_schedules.py: added per-profile schedule endpoints and dispatcher support.
- app/static/app.js: added per-profile Schedule dialog, reused freshness editor helpers, and compact lineage timestamp formatting.
- app/static/style.css: added row action/status and schedule dialog styling.
- docs/agent_handoff.md: updated current repo handoff.

## Commands And Checks
- `node --check app/static/app.js`: passed.
- `python3.12 -m compileall app/database.py app/models.py app/routers/email_schedules.py`: passed.
- FastAPI TestClient schedule endpoint smoke test: passed.
- `git diff --check`: passed.
- Browser smoke test on a temporary local server: passed for Email Schedule dialog and Lineage freshness controls.
- Old SQLite schema migration smoke test: passed after moving `idx_email_schedules_person` out of the base schema.

## Open Questions
- None blocking.

## Next Step
Have the user restart the app on the existing Windows database and confirm startup completes.
