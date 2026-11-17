# 2026-11-17: HTML report refresh Flows: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-17 08:18
- Tested code revision: the commit that adds this package, on `main` at
  `83e3afe` (tested as the working tree before commit)
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`, Node 22.23, Playwright 1.63 (Node) driving Chromium build 1243. No
  Windows, BI desktop, controller or GitHub
- Overall finding: every planned automated case passes and the usability
  walk found no page error (synthetic only). The controller itself, a real
  push and GitHub Pages are not exercised; see the limitations.

## What was asked

The owner asked for a small HTML section in Flows that refreshes repositories
which are themselves HTML reports compiled to Pages. The brief came from the
owner's office assistant, and the owner added three points: the tool must be
flexible because the repositories are internal enterprise ones; ideally one
creates a Flow and points it at the repository; and these HTML reports are now
part of Reports, so data governance and lineage must pick them up.

## Change

- `app/html_reports.py`: the controller settings and their checks, the
  fail-closed command builder, the `METRONOME_STAGE` parser and stage record,
  run summaries and notices, and the Reports sync.
- Settings: seven host-only values under **Flows > HTML reports**: controller
  repository link, controller script, report root, allowed report IDs, the
  argument pattern, dry-run arguments and live arguments. The report list and
  arguments are settings because the controller's README lives in an internal
  repository this session cannot read.
- Flows: an HTML report Flow is a run-only Python Flow with an `html_report`
  block (report ID, Dry run or Live, repository and site links). The server
  builds the controller command on save and again when each run is queued, and
  ignores any script, arguments or Python a request sends. New **Dry run**
  queuing, one refresh per report at a time, and stage parsing as console
  lines arrive (`flow_runs.stages_json`).
- UI: **Create flow > HTML report**, a three-step builder, the **HTML** list
  group with **Run · publishes** / **Dry run** and a confirmation before a live
  run, the run log's stage table and notices, and the history line.
- Reports and lineage: `reports.report_kind`, `refresh_flow_id`,
  `repository_url` and `site_url`. HTML reports are listed with health from
  their refreshes, links, owner and schedule. Names never merge with a Power BI
  report. The Power BI sync, usage and report scan leave them alone, Pipelines
  draws their chain, and Power BI and full-pipeline refresh are refused for
  them.
- Docs: [docs/html_reports.md](../../../html_reports.md) (set-up, migration
  and deployment steps, the controller contract, rollback), with pointers
  from the README and the Python-script Flow docs.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| H-01 to H-19, R-01 | The `python tools/check.py verify` command in the plan | Working tree on `83e3afe`, Linux | PASS: 297 passed, 0 failed, 0 skipped, 90 s. 2 warnings, the installed Starlette test client's deprecation notices | `.test-runs/20261117T081624072Z-4243-70c9e1ab/result.json` |
| H-20, R-02 | `for f in tests/test_*.mjs; do node "$f"; done` | Working tree, Node 22.23 | PASS: 30 of 31. `tests/test_gemini_extension.mjs` fails identically on `main` here, because the `pg` package of the Gemini integration is not installed in this container | Console output |
| U-01 to U-08 | Playwright walk described in the plan (scripts kept outside the repository) | Working tree, Chromium 1243, 1280 × 900 | PASS, details below | Console output; screenshots kept outside the repository |

### Usability walk

1. **U-01**: the **HTML** group listed both Flows with their report IDs,
   Site/Repository links and mode labels. **Trade-in value dashboard** read
   **Run · publishes**, and the running dry run was under **Flows in
   execution** with "Validate data".
2. **U-02**: the builder opened in three steps. The Controller step showed
   the controller link, installed path, report root and "The worker's default
   Python". The command read `python refresh.py --report
   smp_sellout_weekly_html --report-root <root> --dry-run`.
3. **U-03**:
   - Run 1, live, succeeded: four stages (42 s, 7 s, 28 s, 14 s), total
     1m 36s, and "Pushed to GitHub. GitHub Pages may still be deploying".
   - Run 2, failed: **Validate data** failed; "1 METRONOME_STAGE line(s)
     could not be read and were ignored".
   - Run 3, running: **Validate data** was running, its elapsed time counting
     on reload ("1m 32s so far"), with the total "so far".
4. **U-04**: history read "HTML report", "Dry run · no publishing · 1 of 2
   stage(s) succeeded", and for run 2 "Live · publishes · 1 of 2 stage(s)
   succeeded · failed at Validate data", next to the exit code.
5. **U-05**:
   - Reports: "1 Power BI reports and 2 HTML reports". The HTML badge,
     "Daily 07:30 Dubai", and **Degraded** for the failed live Flow.
   - The report's panel offered **Open published site** and **Repository**
     and named its Flow.
   - Lineage drew controller → Flow (last refresh failed) → report → site,
     with **Refresh report** and **Refresh full pipeline** disabled.
6. **U-06**: Settings listed the seven values with their sources and "Applies
   to new runs".
7. **U-07**: `https://user:secret@…` was refused: "The report repository must
   not contain a user name, password or token". The typed name stayed. After
   correcting the link the Flow saved ("Flow saved") and the list returned.
8. **U-08**: **Run · publishes** asked "Run "Trade-in value dashboard" live?
   … publishes it to its existing GitHub repository. A successful push does
   not mean GitHub Pages has finished deploying." Dismissing queued nothing.
   **More > Dry run · no publishing** queued run 4 with "Dry run queued for
   smp_trade_in_value_html. Nothing will be published." No worker runs in the
   container, so the run stayed queued and nothing executed.

The only browser console error was the deliberate 422 of the refused save. The
walk led to three fixes, and the walk was then rerun on the fixed code:

- The Dry run / Live choice was laid out with the Python builder's own field
  styles.
- A CSS class shared by the list label and that field was renamed.
- The list's live-activity refresh kept the **Run · publishes** label instead
  of resetting it to **Run**.

## Findings, limitations and retests

- The first focused run failed 14 existing tests. They call `queue_run()`
  directly, where FastAPI's `Query(False)` default object is truthy, so every
  call asked for a dry run. The parameter now uses `Annotated[bool, Query()] =
  False`, the router's own pattern. The rerun passed: 97 tests in those
  files, then a 283-test set.
- Review before commit found that a signed remote test of a live HTML report
  Flow would have published. Remote tests of HTML report Flows are now always
  dry runs (H-07), and the plan's set gained `tests/test_remote_flow_control.py`;
  the final run above is 297 tests.
- Four existing Node tests load slices of `app.js`. The list helpers were
  moved inside the list's code, and the Run label now travels on the button as
  `data-idle-label`.
- Synthetic only:
  - The controller, its README arguments and its report IDs are unknown here.
    The defaults (`--report {report} --report-root {report_root}`,
    `--dry-run`) must be checked against the README when Settings are filled
    in on the host PC.
  - No real push, Git sign-in or GitHub Pages deployment was exercised.
  - The Windows desktop launcher is the existing run-only path, unchanged and
    not run here.
- An HTML report's lineage starts at its Flow: Metronome does not read the
  controller's own data sources.
- The run log refreshes every two seconds, so a running stage's elapsed time
  advances in two-second steps.

## Review round on the first head (`8e0ff12`)

The automated Codex review on the PR raised four findings. Each was confirmed
in the code, fixed and covered by a test (H-21 to H-23). The four new tests
were first run against the reviewed code, where all four failed, then on the
fix:

| Finding | Fix |
| --- | --- |
| P1: a live run waiting for a worker could be restarted by **Run** after the Flow was switched to Dry run, without the live confirmation | **Run** resumes a waiting refresh only in the mode asked for (the request's dry run, else the Flow's saved mode); otherwise 409 |
| P2: Reports and lineage counted runs of the Flow's earlier report ID | Both lookups count only runs of the current report ID |
| P2: a pattern that already quoted `"{report_root}"` quoted it twice | The pattern is split first, placeholders are filled per argument, and each argument is written back once; worker tokens are always quoted. The builder previews the server's exact command |
| P2: an inferred stage start went through the host's local time (four hours early in Dubai) | Subtract the duration as a `timedelta` on the UTC instant |

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| H-21 to H-23 before the fix | The four new tests with `app/` at `8e0ff12` | Linux | FAIL: 4 failed, as expected | Console output |
| H-01 to H-23, R-01 | The plan's `verify` command | Working tree on `8e0ff12` | 298 passed, 1 failed: `test_each_run_rebuilds_the_command_and_fails_closed` expected `--run {run_id}`; worker tokens are now quoted on purpose, so its expectation became `--run "{run_id}"` | `.test-runs/20261117T082459806Z-4999-0bf412f5/result.json` |
| Failed case and companions | `verify` on `tests/test_html_reports.py`, `tests/test_flow_python.py`, `tests/test_remote_flow_control.py`, `tests/test_settings_flows.py` | Same, test updated | PASS: 116 passed, 2 Starlette warnings | `.test-runs/20261117T082641194Z-5426-86b0d0a0/result.json` |
| H-20, R-02 | `for f in tests/test_*.mjs; do node "$f"; done` | Same | PASS: 30 of 31; Gemini as above | Console output |

## Merge evidence

Final-head CI is pending at this cutoff. Before the head-pinned merge, the
PR's testing section records the `Merge ready` run URL, the tested head SHA
and its result.
