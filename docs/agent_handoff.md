# Agent Handoff

## Current Objective
Add a dashboard "Fix This First" triage panel that explains the highest-priority alerts.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit: e3a5507 Update handoff for usage metrics
- Public repo: yes
- Push status: pending commit and push for triage work

## Decisions Made
- Action ranking stays deterministic and API-owned, not hidden in frontend-only logic.
- `impact_views_30d` is the dominant triage signal: one weighted view is worth more than issue-type and age tie-breakers combined.
- Fix-first scoring also considers issue type, days in problem state, unassigned ownership, affected report count, and source/report stale gap.
- The dashboard shows the top three active actions above the Alerts table with short reasons and a CTA.
- CTA opens the relevant asset when ownership exists; for unassigned actions it scrolls to the table row and focuses the owner dropdown.

## Files Changed
- app/models.py: added triage fields to `ActionOut`.
- app/routers/actions.py: computes triage score, rank, reasons, and CTA for visible actions.
- app/static/app.js: renders the Fix This First panel and binds CTA interactions.
- app/static/style.css: styling for the triage panel.
- docs/metric_contracts.md: added Fix This First rank contract.
- docs/agent_handoff.md: updated current handoff.

## Commands And Checks
- `git pull --ff-only`: already up to date before edits.
- `PYTHONPYCACHEPREFIX=/private/tmp/dg-pycache python3 -m compileall app`: passed.
- `node --check app/static/app.js`: passed.
- `git diff --check`: passed.
- `node .../impeccable/scripts/detect.mjs --json --fast app/static`: warnings only, matching pre-existing classes of issues (side accent borders, layout transitions, single-font heuristic).
- Triage scoring smoke test: not run in local system Python because `pydantic` is not installed in that interpreter.
- Not run: full FastAPI server/browser QA, because app startup begins the scheduler and can dispatch due scheduled emails.

## Open Questions
- No blocker.

## Next Step
After deploy, review the first few Fix This First picks with real usage data to tune issue-type weights if the order feels off.
