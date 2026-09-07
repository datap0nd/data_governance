# Users, TMDL Checker retirement and refresh recovery: test report

- Plan: [test-plan.md](test-plan.md).
- Local evidence cutoff: 2026-09-07 20:39 UTC (2026-09-08 in Dubai).
- Tested implementation: `8f1bcce86ab29cdf895b82dd4dc61c0f8a4445fe`.
  Focused backend runs preceded that commit with the same relevant source;
  the final frontend checks ran against the commit itself.
- Environment: Windows, Python 3.12.14, Node 24.19.0, Playwright 1.62.0,
  Chrome 152.0.7977.76. SQL and portal operations use fictional fixtures.
- Cutoff finding: affected checks pass. The full Python run has four failures
  under investigation at the cutoff (two PowerShell helper tests and two XLSB
  fixtures). The full Python run and final-head CI
  are pending at this cutoff; their completed results and exact tested SHA must
  be recorded in the PR testing section before merge. No live success claimed.

## Executed checks

Commands below used the sibling checkout's installed Python environment:
`C:\Users\keeoh\Documents\ChatGPT\Metronome\.venv\Scripts\python.exe`.
The source under test is this checkout, not that sibling's source.

| Coverage | Actual command / procedure | Result at cutoff |
| --- | --- | --- |
| Users production browser journey and navigation/removal regressions, U-01–U-05/T-01/D-01 | `python -m pytest tests/test_users_browser.py tests/test_overview_removed.py -q` | **PASS: 12**, 4.84s, final implementation commit. |
| SQL commit ordering, connection/COPY/commit/refresh failures and bound recovery plans, R-01–R-05 | `python -m pytest tests/test_flow_sql_view_refresh.py tests/test_view_refresh.py tests/test_flow_standalone.py -q` | **PASS: 65**, 34.42s, same runtime and tests as implementation commit. |
| Regenerated portable script independence | `python -m pytest tests/test_flow_recordings.py::test_portable_script_dry_run_has_no_metronome_import_or_adjacent_configuration -q` | **PASS: 1**, 3.76s. |
| Query-history isolation and Checker retirement, T-01/T-02 | `python -m pytest tests/test_query_history.py tests/test_tmdl_checker_retired.py -q` | **PASS: 18**, 11.64s. |
| All frontend suites | `Get-ChildItem -Path tests -Filter 'test_*.mjs' \| ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw $_.Name } }` | **PASS: all 22 files**, final implementation commit. |
| JavaScript syntax | `node --check` on `app.js`, `users.js`, `flow_run_log.js`, `flow_recordings.js`, `flow_recording_editor.js`, `flow_recording_model.js` under `app/static/` | **PASS**, all six; combined frontend commands 6.22s. |
| Dashboard scope, D-01 | `git diff 3ccbf8487b96a73b68d538808b401cc6510a63e8 --exit-code -- app/routers/dashboard.py app/static/style.css`; source review of dashboard render/bind functions | **PASS**, no implementation changes. Dashboard preview directory and endpoint experiment removed. |
| Whitespace | `git diff --check` | **PASS**; Git reports only Windows LF/CRLF conversion notices. |
| Full Python regression | `python -m pytest tests -q --basetemp C:\Users\keeoh\.codex\tmp\mufinal1` | **IN PROGRESS**, four failures observed at cutoff. Directory verified absent before starting. Final output, diagnoses and retests belong in the PR; no passing result prewritten. |

## Browser evidence

The browser tests serve the real production HTML/JavaScript with intercepted,
fictional HTTP API responses. They cover form errors/retry, deletion/keep,
keyboard focus, failed list loading, busy-request guards, navigation while a
save is pending, and desktop/mobile layout. No private report or person data
appears in the evidence. `METRONOME_UI_EVIDENCE_DIR` was set to this package's
`evidence` directory for the final focused browser run.

- [Desktop Users](evidence/users-desktop.png).
- [Mobile Users, 390px](evidence/users-mobile.png).

Independent review found a new in-form Users link could discard an unsaved
Flow draft. The final implementation uses plain owner guidance; the final
browser/navigation and frontend checks above ran after that correction.

## Findings and recovery constraints

- Refresh engine-creation failures now retain failed phase/status and checkpoint
  evidence. The synthetic tests execute actual loader/refresh control flow with
  controlled database connections; they cannot establish live permissions.
- A committed portable SQL insertion previously left a barrier that prevented
  refresh-only recovery. The runtime now records confirmed commit separately
  from an unknown outcome and permits matching refresh-only retries.
- Review found regenerated scripts could omit an unfinished view. The final
  regression cases remove, replace and reorder planned identities and corrupt
  checkpoints; all block without erasing recovery files. Missing or legacy
  plan-less journals require reconciliation rather than assumed safety.
- An existing query-history fixture copied the default SQLite database during
  test backup. The fixture now patches the runner's imported database path too.
  Test-created copies were removed; tracked historical backups were preserved.
- Missing local `xlrd==2.0.2` and `ecdsa==0.19.1` were installed from the existing
  CI requirements. These were environment prerequisites, not production changes.

## Unperformed live checks

| IDs / activity | Status | Reason and next action |
| --- | --- | --- |
| U-01–U-05 on the work PC | **NOT RUN** | Test the merged app with disposable profiles; synthetic browser evidence does not prove deployment. |
| R-01/R-03/R-04 with real PostgreSQL | **NOT RUN** | Requires the operator's isolated SQL target, dependent views and controlled failure. Record app/worker revisions, run IDs and protected row-count comparisons per the plan. |
| Real portal authentication/downloads and actual Flow schedules | **NOT RUN** | No work-PC or authenticated business portal run was performed. Regenerate portable scripts before testing. |
| Daily PostgreSQL backup/restore | **N/A** | This release only documents options. It installs no backup job, Backups page, event trigger or SQL grants. |

## Merge evidence

The final PR testing section must retain the completed full local run, any
failures and retests, final CI run URL, tested head SHA and both OS job outcomes.
CI uses Python 3.13 on Ubuntu and Windows with Chromium, Chrome and Edge. Those
results occur after this committed cutoff; the PR merge record supplies the
actual main merge SHA. A successful merge is not a deployment or live test.

## Follow-up evidence, 2026-09-07 20:43 UTC

Final source revision: `642aa11f1e13a927074c9766b6e46a0dd656b0b1`.
The original implementation/runtime is unchanged; three existing recording
preview loaders now include `users.js` before `app.js`. The full local run
continued while this fixture correction was made, so its initial failure and
later focused retest are kept separate. Final CI will cover the complete final
source tree. PR: [#87](https://github.com/datap0nd/data_governance/pull/87).

- **Initial local failures, now resolved in the test environment:** two capacity
  helper cases were blocked by the local Windows PowerShell script policy;
  two XLSB cases failed because `pyxlsb` was absent. No source changes were
  needed. Install the existing CI pin `pyxlsb==1.0.10`; pass
  `PSExecutionPolicyPreference=Bypass` only to the test child process, without
  changing registry or persistent policy. The two parameterized
  `test_installer_slot_profiles_and_ids_match_server_without_running_services`
  cases plus the XLSB nodes in `test_flow_excel_formats.py` and
  `test_flow_local_file.py` then **passed: 4 in 2.05s**.
- **Local browser prerequisite still unavailable:**
  `test_same_portable_pipeline_runs_real_download_on_both_browsers[msedge]`
  failed in **5.04s** because Playwright could not find the Edge distribution.
  No new system browser was installed. CI installs Edge and must pass this case
  before merge. Other skipped browser cases will be reported with full output.
- **Introduced fixture issue, fixed:** the older managed-editor page omitted
  the new Users module, producing `renderUsers is not defined`. Production
  `index.html` already loaded it. All three existing recording-preview loaders
  now include the dependency; the discarded dashboard preview remains absent.
  `python -m pytest tests/test_managed_flow_editor.py::test_editor_method_output_and_failure_recovery tests/test_recording_playback_ui.py tests/test_templates_refresh_preview.py -q`
  **passed: 11 in 22.28s** on the final source. `node --check` on those three
  preview JavaScript files also passed.
- All relative links in the feature inventory, backup discussion and release
  plan/report resolve. Temporary test databases were not committed.
- Full local aggregate results and final-head CI remain pending at this later
  cutoff and must be added to the PR before merge.
