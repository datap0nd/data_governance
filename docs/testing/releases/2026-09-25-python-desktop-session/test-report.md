# Python scripts run as from PowerShell; scripts in any folder: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: [PR #144](https://github.com/datap0nd/data_governance/pull/144)
- Evidence cutoff (UTC): 2026-09-25 12:06
- Tested code revision: `ea3fffd` (the review fixes below) for the application,
  tests, the preview walkthrough and `setup.ps1`. The first round ran at
  `56f6f5b3f0be928f1650859b3c53568187716d66`, and `674fabeee8764a0e50a5919fdfbfcdeaafbe33f4`
  added only preview styling; the frontend contracts, JavaScript syntax,
  PowerShell parse and screenshots ran there. `app/static/*.js`, `setup.ps1`
  and the preview did not change afterwards. This report and its evidence
  files are the only later changes.
- Environment: Linux container (`posix linux`), checkout-owned Python 3.13.12
  `.venv` with the locked dependencies (Playwright 1.62.0, psutil 7.2.2),
  Node.js 22.22.2, Playwright's headless Chromium, and a portable PowerShell
  7.4.6 used only to parse `setup.ps1`.
- Overall finding: every in-scope local check passed on synthetic fixtures.
  Final-head CI is pending at the cutoff.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| S-01–S-17, P-01–P-04, C-03, C-04 | `python tools/check.py verify --test tests/test_flow_desktop_session.py --test tests/test_flow_run_mode.py --test tests/test_env_file.py --test tests/test_flow_python.py --test tests/test_flow_paths.py --test tests/test_flow_live_output.py --test tests/test_flow_portable_script.py --test tests/test_flow_standalone.py --test tests/test_python_scripts_preview.py --test tests/test_flow_worker_startup.py --test tests/test_unattended_update_scripts.py` plus the six `tests/test_flows.py::test_setup_*` cases in the plan, with `--syntax` for `app/flow_desktop_host.py`, `app/flow_desktop_session.py`, `app/flow_python.py`, `app/flow_worker.py`, `app/config.py`, `app/env_file.py`, `app/flow_paths.py`, `app/routers/flows.py`, `app/static/app.js`, `app/static/recording-preview/python-folder.js` and `setup.ps1` (PowerShell on `PATH`) | `56f6f5b`, Linux, Python 3.13.12 | PASS: 106 passed, 2 skipped (`tests/test_flow_paths.py:36` native Windows paths; `tests/test_unattended_update_scripts.py:124` Windows scheduled updater), 2 existing Starlette deprecation warnings, 83 s; all syntax checks passed | `.test-runs/20260925T114909182Z-2790-61c600dd/result.json` |
| S-01–S-21, P-01–P-04, C-03, C-04, U-01–U-06 (review fixes) | The same `python tools/check.py verify` selection and syntax list as the first row, plus `--test tests/test_python_folder_preview.py` | `ea3fffd`, Linux, Python 3.13.12 | PASS: 111 passed, the same 2 Windows-only skips, 2 existing Starlette deprecation warnings, 89 s; all syntax checks passed | `.test-runs/20260925T120358544Z-4171-a1f94eae/result.json` |
| U-01–U-06 | `PREVIEW_EVIDENCE_DIR=docs/testing/releases/2026-09-25-python-desktop-session/evidence python tools/check.py verify --test tests/test_python_folder_preview.py --syntax app/static/recording-preview/python-folder.js` | `674fabe`, headless Chromium | PASS: 1 passed, no page errors, 2.7 s | `.test-runs/20260925T115153494Z-3329-65f6b146/result.json`; [walkthrough](evidence/preview-walkthrough.md) |
| C-01, C-02 | Every `tests/test_*.mjs` except the Gemini extension test (npm-installed; unaffected) with `node`; `node --check` for each `app/static/*.js` and the preview script | `674fabe`, Node.js 22.22.2 | PASS: 24 of 24 contract files; 10 files syntax-clean | Local command output |
| S-13 | `pwsh -NoProfile -Command '[System.Management.Automation.Language.Parser]::ParseFile("setup.ps1", …)'`; expansion of the new task argument and warning strings | `674fabe`, PowerShell 7.4.6 | PASS: 0 parse errors. The argument expands to `"<CodeDir>\app\flow_desktop_host.py" "<USERPROFILE>\.metronome-python-desktop"`; the warning reads "Could not register Metronome_Python_Desktop: …" | Local command output |
| Whitespace | `git diff --cached --check` before the implementation commit | `56f6f5b` | PASS: no whitespace errors | Local command output |

The verifier's `result.json` files are ignored local artifacts; final-head CI
provides the durable GitHub evidence.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Final-head CI | NOT RUN | Starts when this report is pushed | Record the `Merge ready` run URL and tested head SHA in the PR testing section, then merge pinned to that head |

## Usability evidence

- Run-mode builder copy (wording only): the script help now says a script runs
  "as it would from PowerShell: in the signed-in Windows session with your
  normal rights and mapped drives…". The run options add that the BI desktop
  account must be signed in, and a run-mode Flow's Run toast names the signed-in
  session. The builder contract asserts all three; the existing Python preview
  walkthrough (C-03) still passes.
- Managed-folder choice: [preview walkthrough](evidence/preview-walkthrough.md)
  with eight screenshots at 1280×900 and 390×844, covering the default, the
  managed choice, the required Output folder, a relative-path failure with
  focus and preserved values, recovery, dropping an existing managed folder,
  and the narrow layout. **Owner feedback is pending**; `DESIGN.md` requires it
  before the choice is built, so the application is unchanged in this respect.

## Findings, limitations and retests

- Codex reviewed `674fabe` and raised three findings:
  - The release report was missing. It was committed next, in `e8aace3`.
  - A launcher could wait forever if the worker crashed or restarted. Fixed in
    `ea3fffd`: the request carries the Flow deadline, and the worker renews a
    heartbeat from its own thread. The launcher ends the script tree, observed
    descendants and orphans when the deadline has passed (30-second grace) or
    the heartbeat has been silent for two minutes (S-18, S-19, S-20).
  - Each run's checksum came from the up-front check. Fixed in `ea3fffd`: the
    launcher hashes the script just before starting each run (S-21).

- No Windows host, Task Scheduler, desktop session or Excel is available here,
  and live work-PC checks were not requested. The launcher protocol ran end to
  end with the real launcher process standing in for the task. That covered
  the request spool, claim and cancel, the session environment with `.env`,
  output relay, waiting for child processes, the time limit, Stop, launch
  failures and a session that ends mid-run. The `schtasks /Run` call is
  verified as an exact command with a simulated Task Scheduler reply. The task
  registration is verified by source contract, the PowerShell 7.4 parser and
  string expansion; Windows PowerShell 5.1 itself did not run.
- Windows behavior this change relies on, but that these tests cannot show:
  - An interactive, `Limited` task started from the service runs in the
    signed-in session with the standard token, mapped drives and environment.
    The existing headed-worker task (interactive, started with `schtasks /Run`
    from the service) and the Outlook per-run task (interactive, standard
    rights) already rely on the same mechanism.
  - COM programs then behave there as they do from PowerShell.
- S-11 (the session ends mid-run) uses POSIX signals and is skipped on Windows.
  Its Windows equivalent, a sign-out, ends the processes the same way.
- Owner-requested contract changes replaced three pinned assertions. The
  location rule "Python script must be inside `<root>/Python`" now asserts
  that such scripts are accepted, while a destination outside the source
  folder is still refused. The builder contract asserts the new run-mode help
  sentence. `test_run_mode_claim_requires_versioned_worker_capability`
  additionally proves that a worker without `python_script_desktop_v1` never
  claims a run-mode job.
- `setup_ps1_clean.txt`, the legacy setup copy, does not gain the task. The
  update path runs `setup.ps1`.
- The first combined verifier run above omitted the new preview walkthrough;
  it ran separately (U-01–U-06). No test failed in either run. Before those
  runs, the new setup contract failed once as expected because `setup.ps1` was
  not yet edited, and the builder contract failed on the old help sentence
  until its assertion was updated.

## Merge evidence

Pending at the cutoff: the final `Merge ready` run URL and the tested head SHA
go in the PR testing section before the head-pinned merge. The PR merge record
supplies the merge SHA. A merge is not a deployment: `setup.ps1` registers the
task when Metronome is next updated on the BI desktop.
