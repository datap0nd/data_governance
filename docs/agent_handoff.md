# Agent Handoff

## Current Objective

Collect the remote-PC owner's `nerp_remote_runner_connection.md`, then implement
and live-verify NERP as a remote-script module in Flows. Keep the paused
production Flow paused until the user explicitly resumes it, so the ASAP CSV 5
fix still needs a future live Flow run.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest NERP documentation commit: `0db6b44` (`Document NERP remote runner contract`)
- Latest prior feature commit: `a746681` (`Simplify dashboard alerts and recover ASAP frames`)
- Feature commit: `de1b362` (`Improve alert assets and enable report refresh discovery`)
- Public repo: no, private
- Push status: NERP documentation commit verified on `origin/main`
- Preserve untracked `governance.db-shm` and `governance.db-wal`
- Preserve unrelated local changes in `app/flow_gscm.py`, `app/flow_worker.py`,
  and `tests/test_flow_worker_discovery.py`; they were not part of the NERP task.

## Decisions Made

- Alerts identify assets with an inline Power BI, Excel, SQL, or Flow logo. The
  redundant source/report text column is removed.
- Alert owners use a wider control, and decorative purple asset badges are not
  used. Existing severity colors remain semantic.
- A Power BI refresh can discover and persist a missing semantic-model ID from
  the configured workspace before requesting the refresh.
- The dashboard Last Scan card uses the latest completed scan. A newer stopped
  or failed scan must not replace valid report/source counts with null values.
- Best-practice actions stay excluded from operational Alerts, and redundant
  alert families remain collapsed by the existing cleanup logic.
- Pipeline card names, nested field/visual names, schedules, facts, and metadata
  wrap without ellipsis. Secondary metadata uses its own line.
- Visuals and Power BI Tables use a versioned session preference and start
  hidden for both new and existing browser sessions after the update.
- Dashboard Alerts do not expose manual status controls. Scanner presence
  determines whether an alert is active, while Owner assignment remains editable.
- Dashboard Issue badges are neutral and use precise source labels. The date
  column is named First detected because it is based on the action creation date.
- A transient ASAP `Frame was detached` error retries once against the current
  report frame. If the final export already created a staged file, the worker
  recovers that file instead of issuing a duplicate export.
- NERP is a separate remote-script module in Flows beside ASAP and GSCM, not a
  website/report adapter and not a SQL transport.
- NERP starts only allowlisted script IDs through an authenticated remote API.
  It never submits arbitrary paths, Python source, or shell commands.
- A launch acknowledgement is not success. Metronome needs a durable remote run
  ID, incremental state and logs, idempotent start requests, and a terminal
  `succeeded` state with exit code 0 before releasing the next pipeline step.
- Existing finance SQL tables remain downstream pipeline inputs. Runner success
  and optional SQL freshness validation are separate concerns.

## Files Changed

- `app/models.py`, `app/routers/actions.py`: expose asset source type to Alerts.
- `app/scanner/pbi_fetch.py`, `app/routers/reports.py`, `app/routers/lineage.py`:
  lazy semantic-model discovery and enabled report refresh controls.
- `app/static/app.js`, `app/static/style.css`: alert logos, simplified columns,
  wider Owner controls, and neutral styling.
- `app/routers/dashboard.py`: select the latest completed scan for the summary.
- `app/static/app.js`, `app/static/style.css`: Pipeline default visibility and
  non-truncating node layout.
- `app/static/app.js`, `app/static/style.css`: remove Dashboard Status and Open,
  neutralize Issue badges, widen the useful columns, and clarify alert wording.
- `app/flow_worker.py`: recover or safely retry detached ASAP export frames.
- `docs/nerp_remote_runner_handoff.md`: explain the existing Metronome Flow
  model, minimum NERP runner contract, required connection handoff template,
  security boundaries, and live acceptance evidence.
- `tests/test_overview_removed.py`, `tests/test_flow_worker_discovery.py`:
  regression coverage for the Dashboard table and CSV frame replacement.
- `tests/test_actions_dedupe.py`, `tests/test_pbi_fetch.py`,
  `tests/test_report_refresh.py`, `tests/test_dashboard.py`,
  `tests/test_lineage_display.mjs`: regression coverage.

## Commands And Checks

- `/tmp/data-governance-test-env/bin/python -m pytest -q`: 277 passed.
- `node --check app/static/app.js`: passed.
- `/tmp/data-governance-test-env/bin/python -m compileall -q app`: passed.
- `git diff --check`: passed.
- `node tests/test_lineage_display.mjs`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- Current full suite: 280 passed.
- Local rendered Dashboard: six expected headers, no Status header or Open
  button, Notify SLA visible, neutral Issue background, and 180px unclipped
  Owner select. Browser console had no errors.
- Production updater installed build `20260816-234836`. It retried three
  transient 502 download failures, succeeded on attempt four, preserved the
  database, authenticated the ASAP worker browser, and restarted the service.
- Production Dashboard: Asset, Views, First detected, Issue, Owner, and Action
  are the only alert columns; row Open and Status are absent; neutral Issue
  labels, full Owner selects, and Notify SLA are visible.
- Local browser verification with a deliberately long script name: labels and
  metadata had no hidden overflow or ellipsis; default columns were Sources and
  Scripts; Visuals and Power BI Tables could be enabled; no console errors.
- Production updater installed build `20260816-232302`; service restart reported
  success.
- Live Alerts page: logos visible, no Type column, Owner controls fully visible,
  and no decorative purple asset badges.
- Live dashboard: Last Scan displays `36 reports` and `130 sources`, not nulls.
- Live report refresh: request accepted, audit event recorded, and Power BI later
  reported `Completed` with a new completion timestamp.
- Live materialized-view validation: read-only PostgreSQL query returned 487,416
  rows and current week/month maxima. Exact relation modification time was not
  readable with the available database role.
- `git diff --check`: passed for the NERP documentation change.
- GitHub visibility check: `datap0nd/data_governance` is private.
- NERP runtime or network tests: not run because this task only produced the
  contract and no remote connection handoff is available yet.

## Open Questions

- Flow-to-Pipeline production matching remains unverified because the matching
  target tables are not yet present.
- The available PostgreSQL role cannot independently read the materialized
  view's storage modification timestamp.
- The remote runner's real base URL, authentication, endpoint shapes, status
  vocabulary, retention, timeout behavior, and safe test scripts are unknown
  until the remote-PC owner returns `nerp_remote_runner_connection.md`.
- The ASAP CSV recovery is covered locally but has not been verified with a live
  multi-file production Flow because that Flow remains paused and no run was
  authorized in this task.

## Next Step

Send `docs/nerp_remote_runner_handoff.md` to the remote-PC owner and collect the
completed `nerp_remote_runner_connection.md`; then implement the adapter against
the documented real API and execute the full success/failure acceptance path
from the Metronome host before enabling NERP schedules.
