# Agent Handoff

## Current Objective
Keep the Import Data tool split into a one-time table creation step and a recurring import scheduling step for existing tables.

## Repo State
- Path: repo root
- Branch: `main`
- Latest commit before this handoff: `82f11b2 Condense materialized view import controls`
- Public repo: treat as public; keep pushed files generic and free of identifying details.
- Push status: current flow change pending commit and push at handoff update time.

## Decisions Made
- Creating a new target table is a one-time SQL write from the app and does not create a Python scheduling script.
- After a new table is created, the UI switches back to Existing table with the created table selected, so the user can configure a recurring append or truncate-and-replace script.
- Existing target tables go straight from file preview to recurring import options.
- Generated Python scripts are for recurring imports only and accept append or replace mode, not create mode.
- Selected materialized views are included in generated scripts after the SQL write. The separate refresh button is only for a manual immediate refresh.
- The staged upload is preserved after one-time table creation so the follow-up recurring script can be generated from the same upload.

## Files Changed
- `README.md`: documents the split between one-time table creation and recurring append/replace Prefect scripts.
- `app/routers/data_import.py`: rejects create-mode script generation, narrows generated scripts to append/replace, and preserves the staged upload after one-time create.
- `app/static/app.js`: removes the old Load now flow, adds Create table now for new tables, shows recurring options only for existing tables, and clarifies MV/script labels.
- `app/static/index.html`: bumps the app JS cache version to `v=44`, bumps the CSS cache version to `v=42`, and keeps the touched shell labels generic.
- `app/static/style.css`: keeps the touched design-system comment and logo selector names generic.
- `docs/agent_handoff.md`: updates the durable handoff context for this flow.

## Commands And Checks
- `python3 -m py_compile app/routers/data_import.py app/config.py`: passed with system Python.
- `node --check app/static/app.js`: passed with system Node.
- `git diff --check`: passed.
- Bundled Python `py_compile app/routers/data_import.py app/config.py`: passed.
- Bundled Node `--check app/static/app.js`: passed.
- Generated-script template compile check with import stubs: passed.
- Browser harness on `http://127.0.0.1:8765/static/import_flow_harness.html#dataimport`: passed. Verified no Load now button, existing tables go straight to recurring setup, selected MVs are sent in script generation, new table creation calls create mode without MV refresh, post-create UI switches to existing-table scheduling, and script generation never sends create mode.
- Not run: live PostgreSQL create/import/MV refresh, because no configured local target PostgreSQL connection was available in this shell.

## Open Questions
- Confirm on the deployment machine that `DG_UPLOAD_PGUSER` can create tables, insert/truncate the target tables, query `pg_matviews`, and refresh the selected materialized views.
- Decide whether scheduled Prefect deployments should use the generated copied file path as-is or override `source_file` with a stable upstream file path.

## Next Step
Install updated requirements on the deployment machine if not already done, restart the app, open Tools > Import Data, create or select a non-critical table, generate an append/replace script with one selected materialized view, then serve it through Prefect with the desired schedule.
