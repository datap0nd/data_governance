# Progress-based download and render waits; recorded-flow gate wording: test plan

- Change/PR: two owner reports.
  1. **A Flow's download keeps going as long as it is moving.** Before this
     change the worker failed a portal download after 10 minutes without any
     change in its staging file, after 60 minutes even while the file was
     still growing, and after 15 minutes without a first file; ASAP report
     rendering had a 30-minute cap; none of these waits posted a progress
     event, so the run log was silent for the whole transfer. Now
     (`app/flow_worker.py`): every 5 minutes (`DOWNLOAD_PROGRESS_CHECK_SECONDS`)
     the worker compares the staging folder with the previous check and posts
     one run event: `download_waiting` (no file yet), `download_progress`
     (file name, size, bytes added since the last check, minutes elapsed) or
     `download_stall_warning` (check *k* of 3). A growing file is never
     capped. Three consecutive checks without any change fail the file after
     15 minutes (`DOWNLOAD_STALL_TIMEOUT_SECONDS`), and the existing per-file
     retry restarts it. GSCM Excel and recorded non-ASAP downloads keep the
     browser as completion authority and gain a reporting-only helper thread
     (`_StagingProgressWatch`) that posts the same events; their
     Playwright download-event budget is now `DOWNLOAD_EVENT_TIMEOUT_SECONDS`
     (60 min, previously `DOWNLOAD_MAX_TIMEOUT_SECONDS`), and recorded flows
     use the same budget for their download event (was 30 min). ASAP report
     rendering waits indefinitely while the page is open, posting
     `report_rendering` every 5 minutes; it fails only when the page closes
     or when the loading overlay has been clear for 5 minutes without result
     rows (`ASAP_EMPTY_RESULT_SETTLE_SECONDS`, re-run once as before). Catalog
     scans keep a 30-minute overlay bound (`ASAP_SCAN_OVERLAY_TIMEOUT_SECONDS`).
     The Flows list keeps the download phase for the new stages
     (`app/flow_activity.py`); recorded flows report through their step
     (`app/flow_recording_runtime.py`).
  2. **"Run not queued: Record and validate this Flow configuration, or
     approve saving without testing…" must never appear again.** PR #116
     removed that gate; this change retires the last remnant (a recorded Flow
     created already enabled answered `Record and validate the draft before
     enabling its schedule.`) by sharing the post-#116 wording
     (`flow_recordings.NO_RECORDING_DETAIL`, "Choose a saved recording for
     this Flow before running or enabling it.") and deletes the retired
     "Save without testing?" preview (`app/static/recording-preview/save-without-test.*`).
  Files: `app/flow_worker.py`, `app/flow_recording_runtime.py`,
  `app/flow_activity.py`, `app/routers/flows.py`, `app/flow_recordings.py`,
  `README.md`, `docs/flow_workers.md`, `docs/gscm_portal.md`, tests, this
  package and the testing index. PR link: recorded in the test report's Merge
  evidence section.
- Code baseline: `df8d15b` (main after PR #116).
- Related report: [test-report.md](test-report.md)
- Intended environments: Metronome app and Flow workers on the BI desktop
  (Windows, Edge/Chrome, ASAP and GSCM portals); the automated checks run
  anywhere with Python 3.11+ and the locked test dependencies (Chrome channel
  for the synthetic browser cases). Live portal runs are owner checks and are
  not performed by the automated evidence.

## Prerequisites and test data

- Update the app **and every Flow worker** from the merged `main` revision:
  the waits live in the worker, the gate wording in the API. No migration.
  Generated `run_flow.py` headers may report `has not been tested` for
  revisions tested before this release because the worker execution hash
  changed; that is evidence only and never blocks a run.
- Live data: one ASAP Flow whose export is known to take longer than 10
  minutes, one GSCM Excel Flow, and one recorded Flow whose active recording
  was tested before an edit. Do not publish portal URLs, credentials or data.
- Synthetic tests use a fake monotonic clock (`_Clock`) so hour-long waits run
  in milliseconds, a temporary staging folder, and page/frame doubles.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| DW-01 | Run the long ASAP Flow; open its run log while the file transfers. | Every 5 minutes a `Download progress` event: `The ASAP export download is still transferring: <file> is N bytes (+D bytes in the last 5 minutes, M minutes elapsed). The run keeps waiting while the file grows.` The run succeeds even beyond 60 minutes. | run ID, revision; automated `test_staged_download_posts_progress_every_five_minutes_with_byte_deltas`, `test_staged_download_keeps_waiting_past_sixty_minutes_while_the_file_grows` |
| DW-02 | Same Flow while the portal stops streaming (cannot be forced; observe when it happens). | `Download stall warning` events `… has not changed for 5/10 minutes … Check 1 of 3 / 2 of 3; the run fails only after 15 minutes without growth.` Growth resets the count. After 15 minutes without change the file fails with `… stopped growing: no growth for 15 minutes (3 checks) …` and the per-file retry restarts it. | automated `test_staged_download_fails_after_three_zero_growth_checks`, `test_staged_download_growth_resets_the_stall_counter`, `test_export_task_retry_restarts_a_failed_file_and_keeps_its_result` |
| DW-03 | Dashboard link that takes minutes to start. | `Download waiting` events every 5 minutes (`Still waiting for … to create a file … gives up after 15 minutes without a file`), then the unchanged start error if nothing ever appears. | automated `test_staged_download_reports_waiting_before_the_first_file`, `test_dashboard_signal_posts_waiting_events`, `test_download_wait_error_does_not_claim_the_download_completed` |
| DW-04 | Edge native-event handoff (recorded ASAP). | The 60-second browser-to-staging handoff budget is unchanged and bounds only the start; the staging wait receives the step's progress callback. | automated `test_dashboard_edge_event_uses_a_bounded_staging_handoff`, `test_dashboard_edge_event_reports_a_missing_staging_handoff`, `test_recorded_asap_download_uses_scan_path_staging_completion` |
| DW-05 | GSCM Excel Flow with a large export. | The browser stays the completion authority; while it transfers, `Download progress` events name `The GSCM Excel download`; the Playwright download-event budget is 60 minutes. | automated `test_edge_completed_download_watches_staging_while_blocking`, `test_staging_progress_watch_reports_without_failing`, `test_staging_progress_watch_is_inert_without_a_callback`, `test_edge_native_download_event_is_the_completion_authority` |
| RR-01 | ASAP report that renders for longer than 30 minutes. | `Report rendering` events every 5 minutes (`ASAP is still rendering the ASAP report after M minutes; the loading overlay is still visible…`); the export proceeds when rows appear, however long that takes. | automated `test_asap_results_post_still_rendering_every_five_minutes`, `test_asap_results_never_time_out_while_overlay_is_visible` |
| RR-02 | Close the browser page during rendering; separately, a report that renders no rows. | `ASAP page closed while waiting for the ASAP report to render after M minutes.` fails the file. An overlay clear for 5 minutes without rows fails with `ASAP report rows did not render: the loading overlay stayed clear for 300 seconds without result rows…` and RUN is re-tried once as before. Catalog scans keep the 30-minute overlay bound. | automated `test_asap_results_fail_when_the_page_closes`, `test_asap_loading_wait_fails_when_the_page_closes`, `test_asap_results_declare_empty_result_after_overlay_clear_settle`, `test_asap_run_report_retries_once_after_silently_empty_rendering`, `test_asap_loading_wait_keeps_a_bound_only_for_catalog_scans` |
| UI-01 | Flows list while a run is in any of the new stages. | The row keeps the Download phase and shows the latest event message. | automated `test_long_wait_progress_events_keep_the_download_phase` |
| RG-01 | Edit a recorded Flow whose recording was tested (name, file name template, moved managed folder); Save; Run; enable a daily schedule; let the scheduler queue it. | Save 200, `Run` queues the run, enable 200, the scheduler queues a run with the edited settings; no response contains `Record and validate`. | automated `test_edited_tested_recorded_flow_saves_runs_and_schedules_without_gate` |
| RG-02 | Create a recorded Flow with the schedule already active. | 409 `Choose a saved recording for this Flow before running or enabling it.` (the same words a run would use); no code or page in `app/` contains the retired gate wording. | automated `test_new_recorded_flow_enabled_without_recording_names_the_next_action`, `test_recording_test_gate_wording_is_gone` |

## Automated checks

Focused set (Linux container, checkout-owned virtual environment from
`requirements-ci.lock`; CI runs the same files):

```bash
python -m py_compile app/flow_worker.py app/flow_recording_runtime.py app/flow_activity.py app/routers/flows.py app/flow_recordings.py
python -m pytest tests/test_flow_worker_discovery.py tests/test_recording_optional_checks.py tests/test_flow_activity.py tests/test_flow_replay.py \
  "tests/test_flows.py::test_staged_download_waits_for_a_new_stable_file" \
  "tests/test_flows.py::test_staged_download_accepts_an_existing_path_overwritten_by_edge" \
  "tests/test_flows.py::test_download_wait_error_does_not_claim_the_download_completed" \
  "tests/test_flows.py::test_asap_execution_uses_rendered_ui_not_internal_response_url" -q
python -m pytest tests/test_recording_journey.py tests/test_flow_recordings.py -q -k "gate or without_recording or without_confirmation or active_evidence or gating"
```

Equivalent work-PC command:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_worker_discovery.py,tests/test_recording_optional_checks.py,tests/test_flow_activity.py,tests/test_flow_replay.py,tests/test_recording_journey.py,tests/test_flow_recordings.py -SyntaxPath app/flow_worker.py,app/flow_recording_runtime.py,app/flow_activity.py,app/routers/flows.py,app/flow_recordings.py
```

Affected-module regression (not repeated in CI's place; CI's final-head
`Merge ready` is the authoritative full Python regression):
`tests/test_flows.py tests/test_flow_recordings.py tests/test_recording_journey.py tests/test_flow_sql.py tests/test_flow_standalone.py tests/test_recorded_output_storage.py tests/test_flow_parallel.py tests/test_recording_v2_pipeline.py tests/test_recorded_browser_pipeline.py tests/test_recording_clicks.py tests/test_flow_portable_script.py tests/test_flow_handover.py tests/test_gscm.py tests/test_flow_email_delivery.py`.

## Acceptance and cleanup

Accept when the focused set and final-head CI pass. Live acceptance on the BI
desktop additionally requires one ASAP export longer than 10 minutes to
succeed with `Download progress` events in its run log (DW-01) and one edited
recorded Flow to run without a gate (RG-01); record run IDs and the deployed
revision. Cleanup: none. Rollback: reverting restores the 10-minute stall,
60-minute cap and 30-minute render budget; no data format changed.
