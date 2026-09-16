# Python-script Flows: test plan

- Change/PR: PR: pending.
- Scope: new `python` Flow source that runs an ordered chain of Python scripts on the worker and keeps the final CSV/XLSX file or inserts the final CSV into a SQL table with the existing materialized-view refresh; builder, list and run-history UI; documentation.
- Baseline: current `origin/main` when work began (SHA recorded in the report).
- Related report: [test-report.md](test-report.md).
- Environments: isolated Linux Python 3.13 fixtures (checkout-owned `.venv`, `requirements-ci.lock`), Node contract test, fictional browser preview through Playwright; final PR CI supplies the full Python regression. No live, work-PC or portal environment is in scope.

## Prerequisites and test data

Run `python tools/check.py setup` once, then `python tools/check.py preflight`.
Every fixture is synthetic: tests create their own SQLite database, a temporary
Flows root outside the checkout, two or three throw-away `.py` scripts written
by the test itself and, where SQL is involved, monkeypatched `flow_sql` and
`flow_view_refresh` entry points. No PostgreSQL server, portal, mailbox,
credential or real worker is needed. The fictional preview
`app/static/recording-preview/python-scripts.html` serves production `app.js`
with in-memory data and touches no API, worker or file.

## Cases

### API and validation

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| P-01 | `FlowWrite(source_type="python")` with no scripts, a relative path, a non-`.py` file and a wildcard path. | Each save is rejected with the exact message: "Add at least one Python script.", "Python scripts must use absolute paths visible to the worker.", "Python scripts must be .py files.", "Python scripts must name one exact .py file each, without wildcards."; the source-type error names "website report, Outlook attachment, file, or Python scripts". |
| P-02 | Save a Flow listing the same script twice (different casing/slashes) plus a second script; save with 21 scripts. | Duplicates collapse preserving first-seen order; 21 scripts are rejected by the `FlowWrite` schema limit (`max_length=20`, pydantic's "List should have at most 20 items") before validation runs, and `flow_python.normalize_scripts` itself rejects 21 entries with the exact message "Choose at most 20 Python scripts." |
| P-03 | Save with `sql_handoff_enabled=true` and `file_format="xlsx"`; save a file-destination Flow with `xlsx`; save with `transform_enabled=true`. | SQL forces `file_format == "csv"`; file destination keeps `xlsx`; `transform_enabled` is False and `transform_script_path` is None; default `filename_template` is `{flow}.csv` / `{flow}.xlsx`. |
| P-04 | Save with `output_mode="private_snapshot"`; create portal, Outlook and file Flows. | `private_snapshot` is rejected by the existing generic rule; the other sources return `python_scripts == []`. |
| P-05 | `create_flow` for a Python Flow, then `catalog()` and `_build_job`. | `source_adapter == "python_script"`; the hidden `Python` site and `Python scripts` report are absent from the catalog; folder is `<root>/Python/<name>` with `target_folder == <folder>/Downloads`; the job has `execution.required_adapter == "python_script"`, a `python_source` section (`enabled`, ordered `scripts`, `output_format`, `destination`, `timeout_seconds` 3600) and `transformation.enabled is False`; `pipelines.flow_target_resource_key_from_job(job)` is a file key. |
| P-06 | `update_flow` with a reordered/changed script list; change a file Flow's `source_type` to `python`. | Scripts are replaced in order and echoed on the returned flow; the source-category change returns 409. |
| P-07 | `inspect_resume_eligibility` and `_flow_failure_message` for a Python run. | `source_no_resume` with the message "Outlook attachment, file-source and Python-script runs cannot be resumed. Use Run to process the source again, or Retry SQL for a saved file."; the failure context labels the source "Python scripts: a.py → b.py". |
| P-08 | `flow_paths.validate_flow` with enforcement on and a script outside `<root>/Python`; the same with the script inside. | Outside is rejected naming "Python script"; inside passes; `assert_job_paths` applies the same rule to `python_source.scripts`. |
| P-09 | `claim_run` from a worker without the `python_script` adapter, then with it. | Without: no claim; with: the run is claimed (mirror of the local-file capability test). `flow_browser.can_claim` is true without browser capability. |
| P-10 | `flow_groups.module_of` for a Python Flow; `GroupWrite(module="Python")`. | Returns `'Python'`; the literal accepts it. |
| P-11 | After `create_flow`, read `saved["standalone"]["state"]` and run the generated `run_flow.py --dry-run` with `sys.executable`. | State is `current`; the dry run prints `"source": "python"` and creates nothing. |
| P-12 | `tests/test_flow_layout.py` parametrized layout cases with `source == "python"`. | Managed folder layout, ownership manifest and repair behave as for the other sources. |

### Worker execution

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| W-01 | Two fixture scripts (script 1 writes a CSV with a header from nothing; script 2 reads `--input`, adds a column, writes `--output`); run `execute_python_job` with the real `sys.executable`. | Final CSV holds the added column; `steps/step-1-<stem>.csv` holds the intermediate; each step record has index, checksum, exit code 0, duration, stdout; progress stages `python_scripts`, `python_step` (1 of 2, 2 of 2) and `python_complete`; the artifact has `status == "saved"` and `python_steps`. |
| W-02 | Script 2 exits 3 after writing to stderr. | `RuntimeError` names the script, "step 2 of 2", exit code 3 and the stderr text; the run fails. |
| W-03 | A script exits 0 without creating its `--output`. | `RuntimeError` mentions `--output`; the run fails. |
| W-04 | Configure a script path that does not exist. | The run fails before `register_folder` is called and before any run folder exists. |
| W-05 | A script that sleeps past the configured timeout (fixture uses a short `timeout_seconds`). | The step is killed, the run fails naming the script and step, and the timeout is reported. |
| W-06 | Final script writes an `.xlsx` workbook through openpyxl with `file_format == "xlsx"`; separately, a CSV is written while `xlsx` is configured. | Workbook: `detected_format == "xlsx"` with size/checksum recorded; mismatch: the run fails on format validation. |
| W-07 | SQL destination with a final CSV containing a BOM and `;` delimiter; `flow_sql.load_artifacts` and `flow_view_refresh.execute_after_sql` monkeypatched; run through `flow_worker.execute_flow(None, job, ...)`. | The CSV is normalized in place to clean UTF-8 rows; `load_artifacts` receives the one artifact; `execute_after_sql` runs after the load; the success message starts with "Ran 2 Python script(s) and saved" and contains the committed row count. |
| W-08 | SQL destination with a header-less final CSV. | The run fails on strict header validation; SQL is not called. |
| W-09 | `output_mode == "direct_replace"` with real `flow_publish`. | The final file is journal-published into the Flow's target folder; the run folder and `steps` stay in the private artifact store; message starts with "Ran 1 Python script(s)". |
| W-10 | `flow_activity.row_progress` for a Python run at `python_scripts`, `python_step` and `python_complete`. | The work item is labelled "Run scripts"; the acquired count fires on `python_complete`. |
| W-11 | A worker registered without the `python_script` adapter polls while a Python run is queued. | The run stays queued for a capable worker; a `portal_work` browser is never opened for Python jobs. |

### Builder, list and run history (fictional preview)

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| U-01 | Serve `app` locally and open `/static/recording-preview/python-scripts.html` at 1280×900 and 390×844. Choose **Create flow**. | The source picker shows a fifth card **Python scripts** between From file and Record a portal flow; the grid wraps 3+2 without horizontal overflow. |
| U-02 | Open the Python card. Add a second script row, then remove it; add it again and fill both paths. | Rows renumber; Remove is disabled when one row remains; the help text states order, `--output`, `--input` from the second script, the `METRONOME_FLOW_*` variables and in-place execution. |
| U-03 | Switch Output between **Final file** and **SQL table**. | File fields (file type, filename, output mode, destination) hide under SQL and the SQL controls plus the materialized-view refresh block appear; switching file type swaps the filename extension; under SQL the collected payload sends `file_format: "csv"`. |
| U-04 | Choose the preview outcome "validation error" and Save. | "Python scripts must be .py files." is shown beside Save; the form keeps both scripts and the chosen output. |
| U-05 | Choose "saved" and Save. | The Flows list shows a **Python** group; the row's source is the script basenames joined with " → ", type "2 Python script(s)" with "Final CSV → SQL"; the browser cell shows "—". |
| U-06 | Open Run history for the preview Flow. | The failed run shows "Python script clean_orders.py (step 2 of 2) failed with exit code 1: KeyError 'region'"; the succeeded run shows the "Ran 2 Python script(s)" summary; worker mode reads "Python scripts". |
| U-07 | Click Run on the saved Flow. | Toast reads "Run queued. The worker will run the Python scripts in order." |

## Automated checks

Backend focused set, from the checkout with its owned `.venv`:

```bash
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_layout.py --test tests/test_flow_local_file.py --test tests/test_flow_outlook.py --test tests/test_flow_standalone.py --test tests/test_flow_topic_groups.py --test tests/test_flow_activity.py --test tests/test_flow_paths.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py
```

Frontend contract, syntax and preview walk:

```bash
cd /home/user/data_governance && node tests/test_flow_builder_contract.mjs && for f in app/static/*.js app/static/recording-preview/python-scripts.js; do node --check "$f"; done
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_python_scripts_preview.py --test tests/test_flow_local_file.py::test_file_builder_exposes_conditional_sheet_and_portal_only_controls --syntax app/static/app.js
```

Each verifier invocation writes `.test-runs/<id>/result.json`; cite that path.
Playwright/Chrome-based cases in these sets may skip when no browser channel is
available on the runner; the report records what actually happened. Final-head
CI (`Merge ready`) is the authoritative full regression for the application,
test and documentation changes.

## Usability evidence

The fictional preview and its Playwright walk (U-01 to U-07) supply the
usability evidence; screenshots are saved to `PREVIEW_EVIDENCE_DIR` when set.
They demonstrate the builder journey with in-memory data only, not a real
worker run.

## Acceptance and cleanup

Accept after the two focused sets, the Node contract test and final-head
required CI pass, and the report records actual results. Test databases, Flows
roots, scripts and run folders are per-run temporary fixtures removed by the
verifier on success; close the temporary preview server after review. No
settings, schedules or existing Flow data are changed.

Rollback: revert the PR. The added `flows.python_scripts_json` column and the
hidden `Python` site row are inert for other sources; any Python Flow created
before a revert stays in the database and its managed folder and scripts are
left untouched. Existing workers ignore Python jobs because they do not
advertise the `python_script` adapter.
