# 2026-11-11: Settings page, connections and passwords in the app: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 10:55
- Tested code revisions: the main run is `fd2e04b`, whose files are those of
  `37c0ec6` on `main` at `9400191`. The page walk is `63e2aff`. The commits
  after `37c0ec6` change one sentence on the page, its cache version and
  documentation; no Python or test file differs
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv` with the locked CI dependencies, Node 22.23, Chromium driven by the
  container's Playwright for the page walk. The project's own Playwright has
  no browser it can start in this container
- Overall finding: every case in the plan passes (automated, synthetic
  fixtures), and the page walk on a temporary instance found no problem. Tests
  that launch a browser, the PostgreSQL suites and the full regression are
  left to the pull request's CI. Nothing was checked against a real
  PostgreSQL server, Power BI, a portal, Windows data protection or the work
  PC.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Every case in the plan, plus every existing test that does not launch a browser | `python tools/check.py verify` with 119 `tests/test_*.py` files and a `--syntax` check of each of the 50 changed Python files under `app/` | `fd2e04b`, Linux | PASS: 2,583 passed, 0 failed, 67 skipped, 14 third-party deprecation warnings, 715.5 s; 50 syntax checks passed | `.test-runs/20261111T103905684Z-3936-f6d11ac5/result.json` |
| Frontend contracts and script syntax | `for f in tests/test_*.mjs; do node "$f"; done` without `tests/test_gemini_extension.mjs`; `node --check` on every `app/static/*.js` | `fd2e04b`; `node --check app/static/settings.js` again at `63e2aff` | PASS: 27 contract files, every script parses. The left-out file needs npm packages that are not installed here; nothing it covers changed | Console output |
| Whitespace check the scope job runs | `git diff --check origin/main HEAD` | Head of the branch | PASS: no output | Console output |
| Page walk, steps 1, 2, 4 and 5 | Playwright script against a temporary instance on `127.0.0.1:8767` with an empty database | `63e2aff`, Chromium, 1360 px | PASS, details below | Console output; screenshots kept outside the repository |
| Page walk, step 3 | The same instance: add, replace-with-nothing, save, test and clear the probe password | `63e2aff` | PASS, details below | Console output |

The 67 skips are 40 that need Windows (PowerShell, DPAPI, ACLs, services,
NTFS junctions) and 27 that need a disposable PostgreSQL server; the pull
request's Windows and PostgreSQL jobs cover the ones in their selections.

### What the page walk showed

- Seven sections, 89 rows: Connections 17 (16 settings and the portal
  sign-in), Folders 3, Scanner 19, Freshness and alerts 5, Flows 28, Email 9,
  Updates, backups and logs 8. Every row had a label and said when a change
  applies. No section scrolled sideways.
- In six sections one number was changed (Folders has none). An out-of-range
  value was refused beside the field with its range, for example "Enter a
  whole number from 1 to 23."; the corrected value saved, was still there
  after a reload with the "Set here" label, and **Reset to default** restored
  the default. The six were the probe port, how late the daily refresh may
  start, the scan age pipelines need, total active workers, how long a Power
  BI sync counts as current, and the update check interval.
- Searching for "time" listed 11 settings across sections.
- Probe password: saving with nothing typed said "Enter the new value, or
  choose Cancel."; after a save the row read "Saved" with **Replace** and
  **Clear**; the value was in the temporary settings file and in neither the
  page, the settings response nor the Event Log, which held two password
  entries. **Test connection** against a closed port answered "Could not sign
  in to postgres on 127.0.0.1:1 as metronome_ro" with the driver's reason and
  without the password. **Clear** asked first, then removed the line from the
  file.
- The portal sign-in block showed "Not set" with **Add** disabled and the
  note that it can only be saved on the Windows host PC, which is correct for
  this Linux instance.
- As another computer (a forwarded request): the notice "Only the host PC can
  change settings…" was shown and no control in any section could be used.
- No page error or console error in any step.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| The 32 test files that use a browser and were left out of the local run, among them `test_recording_playback_ui`, `test_flow_list_browser`, `test_recording_clicks`, `test_recording_journey`, `test_flow_recordings` and `test_users_browser` | BLOCKED | The project's Playwright finds no browser it can start in this container, and the system Chromium stops under the verifier's temporary folder. They fail the same way on unchanged `main` | The pull request's Python shards run them |
| PostgreSQL 14 and 18 suites | NOT RUN | No PostgreSQL server here; `app/flow_sql.py` changed, so the PostgreSQL jobs run | The pull request's PostgreSQL jobs |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

### Found and fixed while building this

- Three existing tests (`tests/test_flow_capacity.py`, two cases, and
  `tests/test_recording_timing_settings.py`, one case) failed on an earlier
  state of this branch: making the worker-limit routes host-only had removed
  a name from `app/routers/system_flows.py` that those tests replace. The
  name is back and is still what the worker start route calls. They pass in
  the main run above.
- One existing test edited: `tests/test_audit_replay_recording.py`, two
  lines. Two cases replaced a constant in `app/routers/recurrences.py` that
  is now read from Settings; they replace the function that reads it instead.
  Their assertions are unchanged.
- Port numbers were shown with thousands separators ("5,432"). Numbers
  without a unit are now shown plain.

### What is on the page

88 settings: 16 connection values (four of them passwords or tokens), 3
folders, 19 scanner, 5 freshness, 28 Flow, 9 email and 8 update, backup and
log settings. 56 apply immediately, 11 at the next scan and 21 to new runs.
Every default is the value the code used before, so installing this changes
no behaviour until someone saves a setting.

### Behaviour that changes for people

- Changes to app-wide settings are refused from other computers: the Settings
  page, the Flows root, worker and portal limits, the daily refresh time, the
  pipeline insights and full pipeline refresh settings, passwords and
  connection tests. On the host PC, open Metronome as `http://localhost:8000`.
  The older pages' Save buttons show that message to a colleague.
- A database host, user or password saved on the page takes effect without a
  restart. A run a worker has already claimed keeps the shared account and
  host it started with.
- The saved AI key is stored encrypted for the Windows account the web
  service runs as. A key saved earlier is encrypted at the next start. If the
  service account is changed later, the key reads as not set and has to be
  entered again.
- With a service principal, the usage sync now honours the saved number of
  days; before, `DG_PBI_USAGE_DAYS_BACK` reached only the saved-account path.
- An environment value outside a setting's range is brought into range, and
  one that is not a number falls back to the default instead of stopping
  start-up.

### Limits and decisions left open

- Retry SQL, Retry views and Resume keep the limits of the run they continue;
  only a new run picks up a changed limit.
- Generated `run_flow.py` scripts keep the built-in limits, so saving a
  setting does not mark every script stale.
- For network replay the value copied into the job wins over a
  `DG_FLOW_REPLAY=0` set only in a worker's own service environment.
- Two values from the audit list are not on the page. How long a daily source
  stays fresh is written into each source and repeated in three other
  modules; changing one place would make pages disagree. The wide-table
  threshold belongs to a checker that is retired.
- The Flow editor still pre-fills a script time limit of 60 minutes.
- Lowering "Failure screenshots kept" leaves older screenshots on disk; the
  worker never deletes them.
- The Windows account password for the services stays a setup prompt, and
  each person's SQL user and password stay on Users.
- Not exercised anywhere: saving the portal sign-in or the AI key through
  real Windows data protection, a worker on the work PC picking up a changed
  connection, and the scheduler on the work PC moving a job.

## Final assessment

Ready for final-head CI.
