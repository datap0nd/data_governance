# Agent Handoff

## Current Objective
Keep Import Data scripts Prefect-compatible, including UI-defined deployment schedules embedded in generated scripts.

## Repo State
- Path: repo root
- Branch: `main`
- Latest commit before this handoff: `5994f09 Split table creation from import scheduling`
- Public repo: treat as public; keep pushed files generic and free of identifying details.
- Push status: current Prefect schedule change pending commit and push at handoff update time.

## Decisions Made
- Creating a new target table is a one-time SQL write from the app and does not create a Python scheduling script.
- After a new table is created, the UI switches back to Existing table with the created table selected, so the user can configure a recurring append or truncate-and-replace script.
- Existing target tables go straight from file preview to recurring import options.
- Generated Python scripts are for recurring imports only and accept append or replace mode, not create mode.
- Selected materialized views are included in generated scripts after the SQL write. The separate refresh button is only for a manual immediate refresh.
- The staged upload is preserved after one-time table creation so the follow-up recurring script can be generated from the same upload.
- Prefect scheduling is configured in the Import Data UI before script generation. Manual/one-time embeds no automatic schedule; daily, weekly, and custom cron embed a Prefect `Cron` schedule with the selected timezone.
- One-time future scheduling is not modeled with RRule because current Prefect docs say RRule `COUNT` is not supported. Manual/one-time scripts are run once with `python script.py` or served as manual deployments without an automatic schedule.

## Files Changed
- `README.md`: documents UI-defined Prefect schedules in generated scripts.
- `app/routers/data_import.py`: adds schedule request validation, embeds schedule defaults in generated scripts, and makes `--serve` use embedded cron schedules unless overridden.
- `app/static/app.js`: adds Prefect schedule controls for manual, daily, weekly, and custom cron; sends schedule payloads with script generation; displays the resulting schedule.
- `app/static/index.html`: bumps the app JS cache version to `v=45`.
- `docs/agent_handoff.md`: updates the durable handoff context for this flow.

## Commands And Checks
- Prefect docs reviewed: official v3 schedule docs, Python deployment docs, schedule API docs, and local-process serve docs.
- `git diff --check`: passed.
- Bundled Python `py_compile app/routers/data_import.py app/config.py`: passed.
- Bundled Node `--check app/static/app.js`: passed.
- Generated-script template compile check with import stubs and embedded weekly Prefect schedule: passed.
- Browser harness on `http://127.0.0.1:8765/static/import_schedule_harness.html#dataimport`: passed. Verified manual schedule body, daily cron generation, weekly cron generation, custom cron payload, and schedule display in the script result.
- Not run: live PostgreSQL create/import/MV refresh or live Prefect deployment serving, because no configured local target PostgreSQL connection or Prefect server was available in this shell.

## Open Questions
- Confirm on the deployment machine that `DG_UPLOAD_PGUSER` can create tables, insert/truncate the target tables, query `pg_matviews`, and refresh the selected materialized views.
- Decide whether scheduled Prefect deployments should use the generated copied file path as-is or override `source_file` with a stable upstream file path.

## Next Step
Restart the app, open Tools > Import Data, generate a script with each schedule mode against a non-critical table, then run one generated scheduled script with `--serve` on the deployment machine to confirm Prefect registers the expected cron schedule.
