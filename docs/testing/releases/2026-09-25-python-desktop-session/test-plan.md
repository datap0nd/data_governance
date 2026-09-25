# Python scripts run as from PowerShell; scripts in any folder: test plan

- Change/PR: the PR from branch `claude/lucid-cori-l5p6af` (link recorded in
  the report). The owner's "Just run" Python Flow never got past
  `Excel.Application`, because the Flow worker is a Windows service in session
  0, which has no desktop. The owner's requirement: "EXACT SAME BEHAVIOUR AND
  OUTCOME as triggering the script in powershell". Each run-only script now
  starts through the interactive, standard-rights scheduled task
  `Metronome_Python_Desktop` in the signed-in session. Follow-up requests in the
  same task: scripts may live in any folder, even with **Enforce paths** on
  (implemented), and a Python Flow may choose whether to use a managed folder
  (fictional preview only, awaiting owner feedback per `DESIGN.md`).
- Code baseline: `main` `17904de` (PR #143 merged).
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container with the checkout-owned Python 3.13
  `.venv`, Node.js 22, Playwright's headless Chromium for the fictional
  preview, a portable PowerShell 7.4 used only to parse `setup.ps1`, and
  required GitHub Actions CI.

## Prerequisites and test data

Synthetic `.py` scripts in per-run temporary folders. In the tests, Task
Scheduler is replaced by starting the real launcher
(`app/flow_desktop_host.py`) as a separate process with its own "session"
environment, which is what the interactive task does on the work PC. The
preview uses fictional in-memory data. No work PC, desktop session, Excel,
scheduled task or live portal is used.

## Test cases

### Desktop-session execution

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| S-01 | Run a run-mode job through a desktop session: a sibling import, `--sheet "two words"` plus a value, a session-only variable, a worker-only variable, a stale `METRONOME_FLOW_OUTPUT` in the session, a project `.env` value, and `input()` | Events `python_session` → `python_scripts` (session `desktop`, interpreter and reason) → step → `python_step_complete` → `python_complete`. Exact argv; the script folder is the working directory; stdin closed. Session and `.env` values visible; the worker-only and stale variables absent. Checksum computed in the session; request folders removed. | `tests/test_flow_desktop_session.py` |
| S-02 | Two scripts, the second missing | Fails with "does not exist or is not readable" before the first script runs | same file |
| S-03 | A launcher script starts a child and exits | Run waits for the child; both outputs collected | same file |
| S-04 | 4-second time limit; parent and child sleep 60 s | Time-limit error; the child process ends | same file |
| S-05 | Stop requested after the first output line | "stopped" error; the root process ends | same file |
| S-06 | Carriage return, invalid UTF-8, blank lines, stderr, exit 7 | Lines split as the pipe reader splits them; failure names exit 7 and the stderr tail | same file |
| S-07 | A command that cannot start | `OSError`; no request folder left | same file |
| S-08 | Windows never starts the launcher (start timeout 0.5 s) | Fails closed: "make sure that account is signed in"; the request is cancelled and a late launcher runs nothing | same file |
| S-09 | Task Scheduler refuses the task; exact `schtasks` command | The error names `setup.ps1` and no request is left; the command is `System32\schtasks.exe /Run /TN \Metronome_Python_Desktop` | same file |
| S-10 | Pending, expired and cancelled requests | Oldest first, each claimed once, expired and cancelled skipped | same file |
| S-11 | The launcher and the script die mid-run, as a sign-out would (POSIX signals) | "session ended" failure long before the time limit | same file (skipped on Windows) |
| S-12 | Output-line bounds | 8000-byte / 2000-character caps, merged separators, final line flushed | same file |
| S-13 | `setup.ps1`: task name, `-LogonType Interactive -RunLevel Limited`, `-MultipleInstances Parallel`, `-Priority 4`, `pythonw.exe` with `app\flow_desktop_host.py` and the spool folder, task stopped before code replacement, registration failure only warns | Source contract passes; PowerShell parser reports 0 errors; the task argument and warning strings expand as intended | same file; `pwsh` parse and expansion output |
| S-14 | Worker registration and routing | Advertises `python_script_desktop_v1`; uses the desktop session only when `os.name == "nt"`; `execute_flow` never references the launcher, so portable `run_flow.py` files are unchanged | same file |
| S-15 | Claim gate | Adapter-only and run-capability-only workers never claim a run-mode job; a worker with both capabilities does | `tests/test_flow_run_mode.py` |
| S-16 | Existing direct runner and other Python paths | Run-mode, live output, Python Flow, portable and standalone suites pass unchanged | `tests/test_flow_run_mode.py`, `test_flow_live_output.py`, `test_flow_python.py`, `test_flow_portable_script.py`, `test_flow_standalone.py` |
| S-17 | `.env` reader moved to `app/env_file.py` | Same parsing, precedence, value-free diagnostics and error handling | `tests/test_env_file.py` |

### Script location

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| P-01 | Enforce paths on; validate a Python Flow whose scripts are outside `<root>/Python` (list and JSON forms) and its frozen job | Accepted; a target folder outside the source folder is still refused | `tests/test_flow_python.py` |
| P-02 | Enforce paths on; create a Python Flow with scripts in an unrelated folder, build its job, read the Paths impact list | Saved and runnable; the job's enforced policy accepts it; nothing listed for relocation | same file |
| P-03 | Browse upload with enforcement on | Still staged under `Python/.uploads`; transformation uploads unchanged | same file |
| P-04 | Enforcement and relocation suites | Other path rules unchanged | `tests/test_flow_paths.py` |

### Copy and existing journeys

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| C-01 | Builder contracts | Run-mode help says a script runs as it would from PowerShell, in the signed-in session with normal rights and mapped drives; sign-in note; run-mode toast; file-mode toast kept | `node tests/test_flow_builder_contract.mjs` |
| C-02 | All frontend contracts and static JavaScript syntax | Pass | `tests/test_*.mjs`, `node --check app/static/*.js` |
| C-03 | Existing Python preview walkthrough | Unchanged journey passes | `tests/test_python_scripts_preview.py` |
| C-04 | Setup contracts in other suites | Pass | `tests/test_flow_worker_startup.py`, `tests/test_unattended_update_scripts.py`, the setup cases in `tests/test_flows.py` |

### Proposed managed-folder choice (fictional preview; not implemented)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| U-01 | Open `/static/recording-preview/python-folder.html` at 1280×900 (New Python flow) | **Flow folder** sits beside the run mode; **No Metronome folder** is the default; the help says nothing is created on disk | `tests/test_python_folder_preview.py`, screenshot |
| U-02 | Choose **Managed Metronome folder** and type a name | Help names `C:\Metronome\Flows\Python\<name>` | same |
| U-03 | **Produce a file or SQL table** with **No Metronome folder** | A required **Output folder** field appears; its help mentions Enforce paths | same |
| U-04 | Enter a relative folder and create; then enter a UNC path and create | Error beside the actions, field focused, values kept; the UNC path saves and shows in the list | same |
| U-05 | Edit a managed Flow, choose **No Metronome folder**, save, then Open folder | Help and status say the folder stays on disk; Open folder names the scripts' folder instead | same |
| U-06 | 390×844 builder and list | No page-wide horizontal overflow | same |

## Automated checks

`python tools/check.py verify` with the new and affected test files above plus
`--syntax` for every changed Python file, `app/static/app.js` and the preview
script. `setup.ps1` is parsed with PowerShell's own parser, and the Node
contracts run directly. Required final-head CI is the full regression.

## Usability evidence

Two kinds. The run-mode builder copy changed (wording only), walked by the
contracts and the existing preview. The managed-folder choice is a proposed
**new decision** in the Python builder. `DESIGN.md` requires a clickable
fictional preview and owner feedback before building it. This PR ships only
the preview (`app/static/recording-preview/python-folder.html`) and its
walkthrough; the application keeps its current folder behavior until the owner
approves or changes the proposal.

## Acceptance and cleanup

Accepted when every in-scope case has a recorded result, the final-head
`Merge ready` passes with its run URL and head SHA in the PR, and the PR merges
pinned to that head. Fixtures are removed by the verifier on success.

Upgrade: update Metronome as usual. `setup.ps1` registers
`Metronome_Python_Desktop` on every run. A "Just run" Flow needs the BI desktop
account signed in; a locked or disconnected session is fine. Rollback: revert
the PR and update. Run-only scripts then start inside the worker service again,
where COM programs such as Excel cannot start. The leftover task is unused and
can be removed with
`Unregister-ScheduledTask -TaskName Metronome_Python_Desktop -Confirm:$false`.
The spool folder `%USERPROFILE%\.metronome-python-desktop` can be deleted while
no run is active.
