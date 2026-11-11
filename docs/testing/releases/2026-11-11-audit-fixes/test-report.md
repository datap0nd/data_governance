# 2026-11-11: Audit fixes for Flows, scanners and access: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 09:40
- Tested code revisions: `9ee6d44` on `main` at `7e406f2` for the main run;
  `8ccc0ca` in the pull request's first CI run; and the commit this report is
  updated in, which fixes the one failure that run found
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv` with the locked CI dependencies, Node 22.23. The project's Playwright
  has no browser it can start in this container
- Overall finding: every case in the plan passes here (automated, synthetic
  fixtures). The pull request's first CI run found one real failure in an
  existing test that the local selection had left out; it is fixed and
  retested. Tests that launch a browser are left to CI, which is also the
  full regression. Nothing was checked against a real portal, Power BI
  tenant, PostgreSQL server or the work PC.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Every case in the plan, plus existing suites for the changed modules | `python tools/check.py verify` with the eight `tests/test_audit_*.py` files and the 27 existing files listed below, and eight `--syntax` paths | `9ee6d44`, Linux | PASS: 1,257 passed, 0 failed, 1 skipped (NTFS junctions, Windows only), 12 third-party deprecation warnings, 231.9 s; eight syntax checks passed | `.test-runs/20261111T090750422Z-17124-cdcf8fef/result.json` |
| Frontend contracts, including F-14's display test | `for f in tests/test_*.mjs; do node "$f"; done`, leaving out `tests/test_gemini_extension.mjs` | `9ee6d44`, Node 22.23 | PASS: 27 files. The left-out file needs the extension's npm packages, which are not installed here; nothing it covers changed | Console output |
| Full regression, first CI run | Pull request jobs on the first pushed head | `8ccc0ca`, GitHub runners | FAIL: `Python (shard 0)` 1 failed, 1,390 passed, 36 skipped. `tests/test_remote_flow_control.py::test_control_endpoints_use_socket_locality_not_permissive_access` raised `AttributeError`: the remote Flow control check had been changed to need a full request, and this test calls the route with only a peer address. Shard 1, Windows, Frontend and both PostgreSQL jobs passed | [run 42994331230](https://github.com/datap0nd/data_governance/actions/runs/42994331230) |
| Retest of that failure | Reproduced locally (1 failed, 14 passed), fixed in `app/main.py`, then `python tools/check.py verify --test tests/test_remote_flow_control.py --test tests/test_audit_access.py --syntax app/main.py` | The commit this report is updated in, Linux | PASS: 41 passed, 0 failed | `.test-runs/20261111T093541651Z-21951-7bacd9cb/result.json` |
| New tests against the previous code | The eight `tests/test_audit_*.py` files copied into a checkout of `7e406f2` and run with `python -m pytest` | Application code of `7e406f2`, new tests | 242 failed, 120 passed. The failures are the defects; the passes are guards on behaviour that must not change | Console output |

Existing files in the first run: `test_auto_update`, `test_flows`,
`test_flow_sql`, `test_flow_parallel`, `test_flow_replay`,
`test_flow_worker_discovery`, `test_flow_worker_startup`, `test_people`,
`test_user_identity`, `test_user_sql_passwords`, `test_pg_identity_hardening`,
`test_pipeline_insights`, `test_pipelines`, `test_recurrences`,
`test_scan_lifecycle`, `test_scan_status_consumers`,
`test_scanner_observability`, `test_email_alert_summary`,
`test_alert_occurrence_api`, `test_alert_occurrences`,
`test_freshness_inheritance`, `test_pbi_fetch`, `test_pbi_visual_query`,
`test_ai_settings`, `test_log_housekeeping`, `test_data_quality`,
`test_source_identity_resolution`.

Three existing tests were edited because the behaviour they describe changed
on purpose:

- `tests/test_pg_identity_hardening.py`: the test that expected a scan to
  fail and roll back when two active sources share one PostgreSQL identity
  now expects the scan to complete, link the report to the lower-numbered
  source and write a warning naming both (S-01). This is a real change to a
  rule the earlier hardening chose deliberately; see "Behaviour that changes
  for people".
- `tests/test_pipeline_insights.py`: the backup test used a file holding the
  bytes `main` as its database; a backup is now a SQLite snapshot (S-06), so
  the test saves a marker setting and reads it back from the backup.
- `tests/test_user_identity.py`: the scenario now names the test PC as one of
  this PC's own names, which the stricter host check requires (C-07).

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Tests that launch a browser (`test_recording_ranges`, `test_recording_controls`, `test_recording_v2_pipeline`, `test_gscm_dataset_inventory`, `test_flow_recordings`, `test_recorded_browser_pipeline`, `test_recording_optional_checks`, `test_users_browser` and similar) | BLOCKED | In this container the project's Playwright finds no matching browser (`Executable doesn't exist`), and the system Chromium stops with `Socket path too long` under the verifier's temporary folder. The same tests fail the same way on unchanged `main` | The pull request's Python shards run them |
| PostgreSQL 14 and 18 suites | NOT RUN | No PostgreSQL server here | The pull request's PostgreSQL jobs |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

### What was fixed

| Area | Fixed |
| --- | --- |
| Flows | F-01, F-02, F-03, F-04, F-05, F-06, F-07, F-08, F-09, F-10, F-11, F-13, F-14, F-15, F-16, F-17, F-18, F-21, F-22 (partly), F-23 |
| Scanners | S-01 to S-14, S-16 (two of three parts), S-17, S-18, S-19 |
| Access | C-01, C-02, C-04, C-07, C-08 |

### What was not fixed, and why

| ID | Finding | Why it is left |
| --- | --- | --- |
| F-12 | A late file from a first ASAP export attempt could be saved under the next period's name | Suspected from reading the code; confirming it needs a slow export on the real portal |
| F-19 | A saved filter whose key left the catalog is ignored | Failing closed here would stop Flows that run today; it needs the owner's decision |
| F-20 | The SQL server recorded in a job is not compared with the one the worker connects to | Belongs with the connection settings change that follows this one |
| F-22 | A portal hostname is still written in `app/flow_gscm.py`, `app/database.py` and `docs/gscm_portal.md` | Removing it changes the seeded website and its migration; the worker and the error text no longer carry it |
| S-15 | An acknowledged alert is raised again the next day | Whether an acknowledgement should silence later occurrences is a product decision |
| S-16 (third part) | 30-day unique users is a sum of daily counts | The stored data cannot give a true distinct count; it needs a different read from Power BI |
| C-03 | People are identified by a name they pick | By design; a real sign-in is a separate project |
| C-05, C-09 | The AI key is in the app database; changed secrets apply unevenly | Both are part of the Settings and connections change that follows |
| C-06 | The app is served over plain HTTP | Needs a certificate and a decision on how colleagues reach the app |
| C-10 | Smaller credential items | Low; left for the connections change |

### Behaviour that changes for people

- From another computer these are now refused: installing an update or
  switching automatic updates, connecting or disconnecting the Power BI
  account, changing who is told about failed scans, saving the portal sign-in,
  adding or changing an ASAP or GSCM website, opening a folder on the host,
  the AI connection tests, and changing another person's email mapping. On the
  host PC, open Metronome as `http://localhost:8000`.
- Saved network-replay requests are keyed differently (F-02), so each Flow's
  first run after this change exports through the browser once and saves a new
  request.
- A scan no longer fails when two active sources share one PostgreSQL
  identity. It links to the lower-numbered source and writes a warning naming
  both; before, every scan failed until one of them was archived. Restoring an
  archived source onto an identity that is already active is refused, which is
  how that state used to arise.
- A missed daily refresh is caught up after start-up, and a failed install is
  retried after 30 minutes instead of every 90 seconds.
- A Power BI visual export that cannot read a filter no longer sends broader
  data; it fails with the filter level named (F-15).
- Only the three newest recording-test folders per Flow are kept (F-16).

### The failure CI found

The remote Flow control routes were switched from "the peer address is this
PC" to the stricter host check, which reads the request's address and
headers. An existing test calls those routes with nothing but a peer address
and failed. The check now keeps the peer-address rule exactly as it was and
additionally refuses a request that the identity middleware judged not to be
the host's own, which is what stops a cross-site page open on the host PC.
Both the existing test and the new cross-site test pass. The local run had
not included `tests/test_remote_flow_control.py`; it should have, since the
change touched that route.

### Limits of these fixes

- F-05: the worker relaunches its browser only on positive evidence that the
  page is closed or crashed. Killing the Chrome process itself was not tested.
- F-07: after a SQL commit the worker keeps trying to report success for ten
  minutes. If the app stays unreachable longer, the server's own ten-minute
  rule still marks that run failed although its rows are committed. Recorded
  Flows and view-retry runs are not covered.
- F-08: the scheduler checks that a view-refresh plan is runnable; it does not
  hold back a Flow because another run is refreshing the same view.
- F-09: the overlay limit applies to catalog scans only; a Flow run still
  waits as long as the page stays open.
- F-03: a run whose SQL outcome is unknown can still be retried.
- S-02: a scan interrupted half-way is not restarted, and the retries of a
  refused refresh stop six hours after it was due.
- S-03: the Updates page does not yet say that it is waiting to retry.
- S-04 assumes Power BI's "is refreshable" flag is correct for a dataset
  without a schedule; S-17 also restores a report it archived earlier for this
  reason.
- F-22: a manual sign-in bootstrap started without its portal argument, where
  the helper cannot read the app database, now assumes ASAP. `setup.ps1` and
  the documented command always pass the argument.
- C-02: the worker still signs in at a report's own address when its website
  has no sign-in address, and saving a report stays open to everyone.
- S-06: seven small database files are tracked under `backups/` in the
  repository. They look like test leftovers; they are left in place and new
  backups are ignored by git. Backups are still never deleted automatically.

## Final assessment

Ready for final-head CI. Not established by this package: behaviour against
the real portals, Power BI, PostgreSQL with business data, Outlook and the
work PC.
