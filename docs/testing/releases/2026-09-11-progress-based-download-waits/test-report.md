# Progress-based download and render waits; recorded-flow gate wording: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: recorded under Merge evidence.
- Evidence cutoff (UTC): 2026-09-11, before the implementation commit.
- Tested code revision: the working tree that became the implementation
  commit on `claude/epic-euler-frboms` on top of `df8d15b`; the PR testing
  section records the final tested SHA.
- Environment: Linux container, Python 3.11.15 in a checkout-owned virtual
  environment built from `requirements-ci.lock` (`tools/check.ps1` needs
  PowerShell, which the container lacks), preinstalled Chromium 141 aliased to
  the Chrome channel path for the synthetic browser cases. No Windows, portal,
  Outlook or worker is available here.
- Overall finding: automated checks PASS; long waits are driven by a fake
  clock and page doubles; live BI-desktop confirmation (DW-01, RG-01) is an
  owner check and was NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| DW-01…DW-05, RR-01, RR-02, UI-01 (focused) | `python -m pytest tests/test_flow_worker_discovery.py tests/test_recording_optional_checks.py tests/test_flow_activity.py tests/test_flow_replay.py "tests/test_flows.py::test_staged_download_waits_for_a_new_stable_file" "tests/test_flows.py::test_staged_download_accepts_an_existing_path_overwritten_by_edge" "tests/test_flows.py::test_download_wait_error_does_not_claim_the_download_completed" "tests/test_flows.py::test_asap_execution_uses_rendered_ui_not_internal_response_url" -q` | working tree, Linux venv | PASS: 243 passed, 1 skipped, 2 warnings in 136.51 s (24 new cases) | Progress every 5 fake minutes with `(+N bytes …, M minutes elapsed)`; 13 progress events and success across 65 fake minutes of growth; arrival then two stall warnings (`Check 1 of 3`, `Check 2 of 3`) and failure `no growth for 15 minutes (3 checks)` naming `export.crdownload (10 bytes)` without the word "completed"; growth resets the count; two waiting events then the unchanged start error; dashboard signal waiting events; the watch thread posts `The GSCM Excel download is still transferring: GUID is …`, survives a raising callback, is inert without one and joins; `_edge_completed_download` reports while the Download double blocks; ASAP rendering posts `report_rendering` every 5 minutes, waits 90 fake minutes under a visible overlay, fails on page closed after 7/11 minutes, declares the empty result after 300 s clear and retries once; scan overlay keeps 1800 s; the list row keeps the Download phase for all four stages. |
| RG-01, RG-02 (gate) | `python -m pytest tests/test_recording_journey.py tests/test_flow_recordings.py -q -k "gate or without_recording or without_confirmation or active_evidence or gating"` | same | PASS: 6 passed in 52.49 s (first run of the new journey case used a `.csv` template on an xlsx Flow and answered 422; corrected to `.xlsx`, `1 passed in 15.77 s`) | Edited tested Flow saves (200, `tested` False), `POST /run` queues with `tested` False, `PATCH /enabled` 200, `queue_due_flows` queues one run with the edited template and no `last_error`; create-time 409 uses `NO_RECORDING_DETAIL`; no `Record and validate`, `approve saving without testing`, `Save without testing` or `allow_untested_recording` text anywhere under `app/`. |
| Baseline of the affected files before the change | same focused command on `df8d15b` (working tree before the worker patch) | Linux venv | 35 failed, 185 passed after the worker patch and before the test updates (all fixed-arity test doubles and the old timeout/message pins); 0 failed after the test updates | Establishes that every changed test pinned the old semantics. |
| Syntax | `python -m py_compile app/flow_worker.py app/flow_recording_runtime.py app/flow_activity.py app/routers/flows.py app/flow_recordings.py` | same | PASS | Console. |
| Regression: affected modules | `python -m pytest tests/test_flows.py tests/test_flow_recordings.py tests/test_recording_journey.py tests/test_flow_sql.py tests/test_flow_standalone.py tests/test_recorded_output_storage.py tests/test_flow_parallel.py tests/test_recording_v2_pipeline.py tests/test_recorded_browser_pipeline.py tests/test_recording_clicks.py tests/test_flow_portable_script.py tests/test_flow_handover.py tests/test_gscm.py tests/test_flow_email_delivery.py -q` | same | REGRESSION_RESULT_PLACEHOLDER | Console. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| DW-01, DW-02, RR-01 live | NOT RUN | No portal, browser worker or Windows desktop in the container; the transfer and rendering are simulated with a fake clock. | On the BI desktop, update app and workers, run one ASAP export longer than 10 minutes and record run ID, revision and the `Download progress` events observed. |
| RG-01 live | NOT RUN | No BI desktop. | Edit one previously tested recorded Flow, save, run and enable it; record run ID and revision. |

## Findings, limitations and retests

- The first check after a file appears counts as growth (the file itself is new
  to the folder), so a file that never changes fails at the fourth check, 15
  minutes after its arrival was reported; the stall tests encode that.
- The reporting-only watch for native downloads polls every 0.5 s from a daemon
  thread and only reads the folder; the test double's `failure()` blocks for
  1.2 s so at least two polls land. A raising progress callback is swallowed
  by the watch, so reporting can never break the transfer.
- No ASAP error-dialog detection was added: nothing in the code identifies a
  portal error element today, and guessed selectors could create new false
  failures. Page closure and the 5-minute empty-result settle are the only
  exits from an uncapped render wait.
- `tests/test_flows.py:4746` still stores the old `did not render within 600
  seconds` text as a historical run error fixture; it is data, not an
  assertion about the worker.

## Merge evidence

Final-head CI is pending at this cutoff. Record its run URL and exact head SHA
in the PR before merging; the PR's merge record supplies the merge SHA.
