# Python-script arguments and setup fixes: test plan

- Change/PR: [PR #134](https://github.com/datap0nd/data_governance/pull/134).
- Scope: per-script **Arguments** (one line, added to the command before Metronome's `--input`/`--output`, with `{flow}`, `{run_id}`, `{date}` and `{value}` tokens) and **Values** (the script runs once per value; every run of the last script is a deliverable, so the run becomes a bundle) for Python-script Flows across API, storage, job, worker, run events, labels, emails and the builder/list/run-history UI; two `setup.ps1` fixes: the self-elevated window is visible for an interactive user and the SSO authentication helper reads the browser channel from the local database when the stopped app's API is unreachable.
- Baseline: `origin/main` `916434745d130fb5cb7026a36960b88f23aac1dc` (PR #133 merged).
- Related report: [test-report.md](test-report.md).
- Environments: isolated Linux Python 3.13 fixtures (checkout-owned `.venv`, `requirements-ci.lock`), Node contract test, fictional browser preview through Playwright; final PR CI supplies the full Python regression. No live, work-PC, portal or PowerShell environment is in scope; the `setup.ps1` change is checked by reading its lines because Linux runners have no PowerShell.

## Prerequisites and test data

Run `python tools/check.py setup` once, then `python tools/check.py preflight`.
Every fixture is synthetic: tests create their own SQLite database, a
temporary Flows root outside the checkout and throw-away `.py` scripts that
echo their `sys.argv` and `METRONOME_FLOW_*` variables into their `--output`
file; SQL insertion and the view refresh are monkeypatched. The authentication
helper test monkeypatches the API client and `authenticate_site`, so no
browser opens. The fictional preview
`app/static/recording-preview/python-scripts.html` serves production `app.js`
with in-memory data and touches no API, worker or file.

## Cases

### Arguments and values: contract, storage and worker

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| A-01 | `FlowWrite(source_type="python")` with `python_script_arguments` shorter and longer than `python_scripts`, with padded spaces, and with a blank script row that carries arguments and values. | Arguments are padded with "" or truncated to the scripts' length and stripped; the blank row is dropped together with its arguments and values; `python_script_values` is padded with `[]`. |
| A-02 | Save arguments with a line break, a tab, 2001 characters and an unclosed quote. | Rejected with exactly "Script arguments must be a single line.", "Script arguments must be 2000 characters or fewer." and "Script arguments have an unclosed quote."; the builder shows the message beside Save. |
| A-03 | Save the same script twice with the same arguments; twice with different arguments; twice with different values. | Identical (path, arguments, values) rows collapse; different arguments or values keep both rows in order. |
| A-04 | Save values with surrounding spaces, blank lines, a duplicate, 201 entries, a multi-line entry and a 501-character entry. | Values are stripped, blanks dropped and duplicates kept; 201 entries are rejected with "Choose at most 200 values per script." and the bad entries with "Each value must be a single line of 500 characters or fewer." |
| A-05 | Save a file-destination Flow whose last script has two values with the templates `{flow}.csv`, `orders.csv`, `{flow}_{value}.csv` and `{flow}_{index}.csv`; the same with one value, with values on an earlier row, and with the SQL destination. | The first two are rejected with "Several values produce several files: add {value} or {index} to the filename template."; the others save; one value, earlier-row values and SQL need no token. |
| A-06 | `create_flow` / `update_flow` with arguments and values; read the row and `_build_job`; clear the two new columns and read again. | `python_script_arguments` and `python_script_values` round-trip on the returned flow, in `flows.python_script_arguments_json` / `python_script_values_json` and in `job["python_source"]["arguments"]` / `["values"]`; a Flow saved before the feature reads back as `["", ...]` and `[[], ...]` aligned with its scripts; Outlook and file Flows return empty lists. |
| A-07 | Worker: two argv-echo scripts with `-sheet T` and `-sheet "T U" -d {date} -n "{flow}" -r {run_id} -k {keep}`. | Script 1 receives exactly `["-sheet", "T", "--output", <path>]`; script 2 receives `["-sheet", "T U", "-d", <today in Dubai>, "-n", "Python orders", "-r", "31", "-k", "{keep}", "--input", <step 1>, "--output", <final>]`; `python_step`, `python_step_complete` events, the `python_complete` results and the artifact's `python_steps` carry `arguments` (split) and `arguments_text` (rendered); the `python_step` message reads "Running script 1 of 2: run_download.py -sheet T."; the opening event lists the raw arguments per script. |
| A-08 | Worker: row 1 `-sheet {value}` with values `T`, `U`; row 2 `-mode` with the value `a b`. | Three runs in order: `-sheet T`, `-sheet U` (with `--input` = run 1's output) and `-mode "a b"` (the value appended and quoted); `METRONOME_FLOW_VALUE` is `T`, `U`, `a b`; `METRONOME_FLOW_INPUTS` is unset for row 1 and lists both row-1 outputs (path-separator joined) for row 2; `METRONOME_FLOW_STEP`/`STEPS` count runs (1..3 of 3); events say "Running script 2 of 3: fetch.py -sheet U." and the opening event "Running 2 Python script(s) in order, 3 run(s) in total: fetch.py -sheet {value} (2 values) → merge.py -mode (1 value)."; records and events carry `value` and `row`. |
| A-09 | Worker: last row with values `T`, `U`, template `{flow}_{value}.csv`, `output_mode` `direct_replace`, through `execute_flow` with real `flow_publish`. | Two deliverables with `bundle_index` 1/2, `bundle_count` 2, distinct `export_view` (`value:1`, `value:2`) and `python_value`; both validated, both published into the target folder as `Python_orders_T.csv` / `Python_orders_U.csv`; success message "Ran 2 Python script run(s) and saved 2 file(s): Python_orders_T.csv, Python_orders_U.csv."; `python_complete` reports `deliverables` 2. |
| A-10 | Worker: `{index}` template with values `a b`, `a_b`, `x`; `{value}` template with `a b` and `a_b`. | Files `Python_orders_1.csv`..`_3.csv`; the sanitized collision yields `Python_orders_a_b.csv` and `Python_orders_a_b (2).csv`; `timings[-1]["item_count"]` equals the deliverable count. |
| A-11 | Worker: SQL destination, last row with values `T`, `U`, final CSVs written with a BOM and `;` delimiter; `flow_sql.load_artifacts` and `flow_view_refresh.execute_after_sql` monkeypatched. | Both files are normalized in place to clean UTF-8 comma rows; the loader receives both saved artifacts in bundle order; the refresh runs after the load; `sql_insertion_complete` reports 2 files; the success message names both files and the committed rows. |
| A-12 | Worker: a quoted `{value}` span (`-n "{value}"`) with the value `a"b`. | The run fails closed before the script starts: `RuntimeError` "... has invalid arguments: Script arguments have an unclosed quote."; one `python_step_failed` event carries the value; no `python_step` or `python_complete` follows. |
| A-13 | `flow_activity.row_progress` for a running Python job whose last row has three values. | The "Run scripts" phase has total 3 (progress total 6). |
| A-14 | `_flow_failure_message` and `flow_email_delivery._source_label` / `build_message` for a Flow with arguments and values; `_source_label` with unreadable arguments JSON. | The label reads "Python scripts: fetch_orders.py -sheet (3 values) → clean_orders.py -x"; unreadable arguments degrade to the plain script names. |
| A-15 | Email delivery: a succeeded Python run reported with a two-file bundle. | `run_context(...)["files"]` lists both files, the message body names both and both are attached, exactly as for a portal bundle. |
| A-16 | `flow_python` helpers: `split_arguments`, `quote_argument`, `render_arguments`, `script_command`, `step_environment`, `describe`, `aligned_arguments`, `aligned_values`, `run_plan`, `run_count`, `deliverable_count`, `filename_token`. | Windows-style splitting (quoted spans, `""` literal quotes, backslashes untouched, unclosed quote rejected); tokens replaced before splitting and unknown ones kept; a `None` date leaves `{date}` as typed; arguments inserted before `--input`/`--output`; `METRONOME_FLOW_INPUTS`/`METRONOME_FLOW_VALUE` set and stale values dropped; labels `run_download.py -sheet (3 values)`; the flat run plan and counts. |
| A-17 | Existing Python-script cases from the 2026-09-16 package (`tests/test_flow_python.py`, `tests/test_flow_layout.py` with `source == "python"`, `tests/test_flows.py`) with the new message formats. | Unchanged behavior for Flows without arguments or values; `python_source` carries empty aligned `arguments`/`values`; single-file messages read "Ran N script run(s); 1 final CSV file(s): <name> (<size> bytes)." and "Ran N Python script run(s) and saved 1 file(s): <name>." |

### Setup fixes

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| S-01 | Read the self-elevation block of `setup.ps1`. | `$ElevationWindowStyle = if ($Unattended) { 'Hidden' } else { 'Normal' }` precedes `Start-Process powershell.exe $ElevationArguments -Verb RunAs -WindowStyle $ElevationWindowStyle`; the literal `-WindowStyle Hidden` no longer appears in that block; nothing else in the block changed. |
| S-02 | Run `flow_worker.main()` with `--authenticate-url` while `_api` raises `httpx.ConnectError` (the stopped service), with `authenticate_site` captured and the fixture database holding Microsoft Edge. | `authenticate_site` is called with the channel `msedge` read from the database; one stderr line says the channel was read from the local database because the API at `--server` is unreachable; the API was probed once (`attempts=1`). |
| S-03 | The same with a reachable API returning `chrome`; then with the API down and no database file at `DB_PATH`. | The API channel wins silently; without a database the helper raises "no database exists at ... Start the MXAnalytics service or set DG_DB_PATH ..." and creates no file. |
| S-04 | Read `setup.ps1` around the authentication loop. | `$env:DG_DB_PATH = $DbPath` is set before the helper is invoked with `--authenticate-url`, so the helper's fallback opens the service's `governance.db` (kept beside the code folder, not inside it). |

### Builder, list and run history (fictional preview)

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| U-01 | Open `/static/recording-preview/python-scripts.html` at 1280×900 and 390×844, choose **Create flow > Python scripts**. | Each script row shows its number, the path line (input, Browse, Remove), an **Arguments** input (`#flow-python-arguments-N`, placeholder `-sheet T`, "(optional)" hint) and a **Values, one per run (optional)** textarea (`#flow-python-values-N`) with a live run count; the help paragraph explains that arguments precede `--input`/`--output`, that double quotes keep spaces and that `{flow}`, `{date}`, `{run_id}` and `{value}` are replaced per run; no horizontal overflow at either width. |
| U-02 | Add a second row, remove the first, add another. | Renumbering updates the path, arguments and values ids and aria-labels together. |
| U-03 | Type `-sheet {value}` in row 1 and three values one per line; clear a value line. | The count beside the textarea reads "3 runs" and updates on input; the collected payload sends `python_script_arguments` and `python_script_values` aligned with `python_scripts`, values split on newlines, trimmed and blanks dropped. |
| U-04 | Leave a row's path blank but type arguments and values, then Save. | That row is dropped from `python_scripts`, `python_script_arguments` and `python_script_values` together (contract test). |
| U-05 | Choose the preview outcome "Script arguments have an unclosed quote." and Save; then the values error and the filename-token error. | Each message shows beside Save with the form preserved; focus lands on `#flow-python-arguments-1`, `#flow-python-values-1` and `#flow-filename` respectively. |
| U-06 | Save successfully. | The Flows list row reads `fetch_orders.py -sheet (3 values) → clean_orders.py` (a row with plain arguments reads `fetch_orders.py -sheet T`). |
| U-07 | Open Run history. | A run shows "Running script 2 of 4: fetch_orders.py -sheet U" and three deliverables; step events display their `arguments` and `value`. |

## Automated checks

Backend and preview set, from the checkout with its owned `.venv`:

```bash
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_email_delivery.py --test tests/test_flow_layout.py --test tests/test_flow_worker_startup.py --test tests/test_python_scripts_preview.py --test tests/test_flows.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py --syntax app/database.py --syntax app/static/app.js --syntax setup.ps1
```

Frontend contract and syntax:

```bash
cd /home/user/data_governance && node tests/test_flow_builder_contract.mjs && for f in app/static/*.js app/static/recording-preview/python-scripts.js; do node --check "$f"; done; git diff --check
```

Each verifier invocation writes `.test-runs/<id>/result.json`; cite that path.
`--syntax setup.ps1` needs `pwsh`; on a Linux runner without it the verifier
reports the skip and S-01/S-04 rest on
`tests/test_flow_worker_startup.py::test_setup_elevation_window_is_visible_when_interactive_and_the_helper_sees_the_database`,
which reads the exact lines. Playwright/Chrome-based cases may skip when no
browser channel is available; the report records what actually happened.
Final-head CI (`Merge ready`) is the authoritative full regression for the
application, test and documentation changes.

## Usability evidence

The fictional preview and its Playwright walk (U-01 to U-07) supply the
usability evidence; screenshots are saved to `PREVIEW_EVIDENCE_DIR` when set.
They demonstrate the builder journey with in-memory data only, not a real
worker run.

## Acceptance and cleanup

Accept after the verifier set, the Node contract test and final-head required
CI pass, and the report records actual results. Test databases, Flows roots,
scripts and run folders are per-run temporary fixtures removed by the verifier
on success; close the temporary preview server after review. No settings,
schedules or existing Flow data are changed.

Rollback: revert the PR. The added `flows.python_script_arguments_json` and
`flows.python_script_values_json` columns are inert for older code, which
keeps running Python Flows without arguments; a Flow saved with values would
then run its last script once. The `setup.ps1` changes only affect the next
setup run.
