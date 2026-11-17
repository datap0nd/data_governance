# 2026-11-17: HTML report refresh Flows: test plan

- Change/PR: link in the PR's testing section. A new **HTML report** Flow type
  schedules and launches the separately installed dashboard refresh controller
  through the existing Python run-only worker. It shows the controller's
  `METRONOME_STAGE` stages live and in run history, and lists each HTML report
  in Reports and Pipelines. The feature is described in
  [docs/html_reports.md](../../../html_reports.md).
- Code baseline: `main` at `83e3afe`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container, Python 3.13 checkout-owned
  `.venv`, Node 22, Playwright with Chromium. Synthetic fixtures only; the
  controller script is never executed.

## Prerequisites and test data

- `python tools/check.py setup` (or the SessionStart hook) has built `.venv`.
- Automated cases build their own disposable database, a fake controller
  `refresh.py` that is never run, and console lines posted the way a worker
  posts them.
- The usability walk starts the app on a scratch database
  (`DG_ENV_FILE=`, `DG_DB_PATH` and `DG_FLOWS_ROOT` in a scratch folder outside
  the checkout, automatic updates switched off before start). It is seeded
  with fictional people (Ana Costa, Omar Haddad), one Power BI report and two
  HTML report Flows (`smp_trade_in_value_html` live, `smp_sellout_weekly_html`
  dry run). Three runs carry seeded stage lines: one succeeded, one failed with
  a malformed line, one running.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| H-01 | Save unsafe values in **Settings > Flows > HTML reports**: a repository link with a token, a wildcard or `.ps1` controller, a report root with braces, a report ID with a space, arguments with an unknown token or without `{report}`, empty dry-run arguments, an unclosed quote | Nothing is saved and each field names its own problem | `test_settings_refuse_unsafe_controller_values` |
| H-02 | Save the settings from a computer that is not the host PC | 403; the allowed list is unchanged | `test_only_the_host_pc_changes_the_controller_settings` |
| H-03 | Create an HTML report Flow before the controller is set; then with a report outside the allowed list; then with a malformed block (bad ID, unknown mode, extra field, credentials in a link, non-http link, non-Python source) | 409 naming Settings; 409 "not in the allowed list"; validation errors; no Flow saved | `test_a_flow_is_saved_only_for_a_configured_controller_and_an_allowed_report` |
| H-04 | Create and then edit a Flow while sending a script path, arguments and Python of the request's own | The saved and queued command is the controller from Settings with the built arguments and the worker's Python; an edit without the block keeps the Flow an HTML report refresh | `test_the_browser_never_chooses_the_command` |
| H-05 | Edit or pause another person's Flow; edit your own; run someone else's as a dry run | 403, 403, saved, queued | `test_only_the_owner_or_host_edits_and_anyone_may_run` |
| H-06 | Change the arguments, then remove the report from the allowed list, make a scheduled run due, then clear the controller | The next run uses the new arguments; manual and scheduled runs are refused with the reason, which is kept on the Flow; nothing is queued | `test_each_run_rebuilds_the_command_and_fails_closed` |
| H-07 | Dry run of a live Flow, Run of a live Flow with live arguments, a signed remote test of a live Flow, Run of a dry-run Flow, dry run of a plain Python Flow, dry run while a live run waits | Dry runs and the remote test add the dry-run arguments and are marked not publishing; the live run adds the live arguments; 422 for a plain Flow; 409 while a live run waits | `test_dry_runs_are_labelled_and_never_publish` |
| H-08 | Two Flows for the same report ID; queue both | The second is refused, naming the first | `test_one_refresh_per_report_at_a_time` |
| H-09 | Classify 19 console lines: plain, indented, missing space, bad JSON, non-object, string or boolean version, version 2, unknown status, empty stage, completion without, with a negative, boolean, non-finite or too-large duration, valid start, `RUNNING`, valid failure | Only well-formed version-1 events are read; version 2 is ignored; the rest are malformed | `test_parse_event_reads_only_well_formed_version_1_events` |
| H-10 | An event whose stage name carries a URL with a token, a `ghp_` token and `token=`, plus extra fields (password, message, start time) | Only report, stage, status and duration are kept; the name is cleaned; nothing else is read | `test_only_the_used_fields_are_read_and_names_never_carry_credentials` |
| H-11 | Post a batch with an ordinary line, a start, a stderr event, another report's event, a completion, a truncated line and a repeated start; post it again; post a later completion | Counts 4 events, 1 malformed, 1 ignored, 1 duplicate; times are the worker's capture times; the controller's duration is kept; the retry adds nothing; the running stage becomes the live stage; the console keeps the original lines | `test_live_lines_build_the_stage_timeline_once` |
| H-12 | Lines through the worker's own live observer, one carrying a secret environment value | The stages arrive with the secret already masked | `test_worker_console_lines_reach_the_timeline` |
| H-13 | A completion without its start; an unreadable capture time; 105 starts | Start placed by the duration and marked; server time used; 100 stages kept and 5 counted | `test_a_completion_without_its_start_is_placed_by_its_duration` |
| H-14 | Summaries for a failed run whose stages all succeeded, and a stopped run with an open stage | "The run is not a success"; the open stage is **Did not finish** until the run ended; history line says where it stopped; total duration from the run | `test_stage_events_never_turn_a_failed_or_stopped_run_into_a_success` |
| H-15 | Summaries for succeeded runs with a failed stage, an unfinished stage, no events and dropped lines; a dry run; a failed run with a malformed line | Each case gets its notice; a live success says Pages may still be deploying; a dry run says nothing was published | `test_a_success_with_a_doubtful_stage_record_says_so` |
| H-16 | Run history with a failed refresh and a plain Python run | The refresh shows its history line and total; the raw column is never returned; the plain run has no block; the status filter works | `test_history_shows_the_stage_summary_for_refreshes_only` |
| H-17 | Reports after create, a dry run, a live success, a failure and a rename; Power BI refresh; lineage; full-pipeline plan | Kind HTML with links, owner and schedule; health unknown → healthy → degraded; last published only from the live success; Power BI refresh 409; lineage names the Flow; the pipeline plan is blocked; a rename follows | `test_reports_lists_each_html_report_with_its_refresh_health` |
| H-18 | Create a Flow named like a Power BI report; delete a Flow; create one of the same name after removing its folder | 409 and nothing saved; report archived with its links; the new Flow takes the archived report back, without a twin | `test_report_names_never_merge_and_a_deleted_flow_leaves_an_archived_report` |
| H-19 | Power BI sync and usage with an HTML report named like a workspace report | The HTML report is neither matched, given Power BI data nor archived; usage never maps to it | `test_power_bi_sync_and_usage_leave_html_reports_alone` |
| H-21 | A pattern with a quoted `"{report_root}"` (root with a space), a quoted `"{flow}"` and `{date}` | The worker's splitter reads each as one argument; the builder's preview is the server's exact command | `test_each_placeholder_becomes_exactly_one_argument` |
| H-22 | Queue a live run, switch the Flow to Dry run, press Run | 409: the live run must be stopped first; after Stop the new run is a dry run | `test_a_queued_live_run_is_not_resumed_after_the_flow_became_a_dry_run` |
| H-23 | A completion without a start on a host in Dubai time; a Flow moved to another report ID | The inferred start stays UTC; Reports and lineage show no history for the new report ID until it runs | `test_a_completion_without_its_start_is_placed_by_its_duration`, `test_reports_lists_each_html_report_with_its_refresh_health` |
| H-20 | Node: the run log's stage table, command preview, Run labels, source group, confirmation and builder escaping | Running, incomplete, inferred and empty cases render with words; untrusted names and links are escaped; the builder has no script or Python field | `node tests/test_html_reports_display.mjs` |
| R-01 | Regression sets for the touched code (Python Flows, live output, run mode, ownership, settings, Power BI sync and usage, report refresh, pipelines, lineage, scanner, handover, dashboard, remote Flow control) | All pass | The `verify` command below |
| R-02 | Every Node contract test | All pass, as on `main` | `for f in tests/test_*.mjs; do node "$f"; done` |
| U-01 to U-08 | Usability walk below | As listed | Console output and screenshots kept outside the repository |

## Automated checks

```bash
python tools/check.py verify --test tests/test_html_reports.py --test tests/test_flow_python.py --test tests/test_flow_live_output.py --test tests/test_flow_run_mode.py --test tests/test_flow_ownership.py --test tests/test_settings_registry.py --test tests/test_settings_flows.py --test tests/test_audit_probes.py --test tests/test_pbi_usage_reliability.py --test tests/test_pbi_attempt_correlation.py --test tests/test_report_refresh.py --test tests/test_pipelines.py --test tests/test_lineage_depth.py --test tests/test_modular_scanner.py --test tests/test_flow_handover.py --test tests/test_dashboard.py --test tests/test_remote_flow_control.py --syntax app/html_reports.py --syntax app/routers/flows.py --syntax app/routers/reports.py --syntax app/routers/lineage.py --syntax app/routers/pipelines.py --syntax app/static/app.js --syntax app/static/flow_run_log.js --syntax tests/test_html_reports_display.mjs
```

```bash
for f in tests/test_*.mjs; do node "$f"; done
```

Final-head CI (`Merge ready`) is the full regression.

## Usability evidence

A Playwright walk of the running app, with the fictional data above, at
1280 × 900, desktop only:

- **U-01**: Flows list. The **HTML** group shows report ID, Site and
  Repository links, "HTML report refresh" with **Live · publishes to GitHub**
  or **Dry run · never publishes**. A live Flow's button reads
  **Run · publishes**; the running dry run sits under **Flows in execution**
  with its current stage.
- **U-02**: builder for an existing Flow, in three steps: HTML report,
  Controller, Schedule and owner. The Controller step shows links, paths and
  the exact command, and the command follows the Dry run / Live choice.
- **U-03**: expanded logs of the succeeded, failed and running runs. Stage
  table, notices, result from the exit code, total duration; the running stage
  counts up.
- **U-04**: **Run history** shows "HTML report" and the stage summary line.
- **U-05**: **Reports** shows the HTML badge, schedule, health and counts. The
  report's panel offers **Open published site** and **Repository** and names
  its Flow; **View Lineage** draws *controller → Flow → HTML report →
  published site*, with Power BI refresh and full-pipeline refresh unavailable.
- **U-06**: **Settings > Flows > HTML reports** lists the seven settings with
  their help and sources.
- **U-07**: **Create flow > HTML report**. A repository link with a user name
  and password is refused beside Save, and the typed name is kept. Corrected,
  it saves and the list returns.
- **U-08**: **Run · publishes** asks for confirmation, naming GitHub
  Pages; cancelling queues nothing. **More > Dry run · no publishing** queues a
  dry run with a toast saying nothing will be published.

## Acceptance and cleanup

Accepted when every case above passes on the final head and `Merge ready` is
green. The walk's scratch database, folders and screenshots stay outside the
repository and are deleted with the session. Nothing on the work PC is
touched.

Rollback: pause every HTML report Flow, then revert the PR. Older code would
run a paused Flow's saved command as a plain run-only Python Flow, and would
treat HTML reports as Power BI reports that the next sync archives.
