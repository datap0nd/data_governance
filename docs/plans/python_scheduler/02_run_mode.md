# Stage 2 — "Just run it" Python Flows

Scope: storage, API, worker, runner and the Python builder. The builder journey
changes, so implementation follows the owner-approved preview.

## Behavior

- A Python Flow has a run mode: **Just run the scripts** (`run`, the default for
  new Flows) or **Produce a file or SQL table** (`outputs`, today's contract,
  unchanged).
- In run mode each script row runs in order, once per value, exactly as typed:
  `[interpreter, script, *arguments]` — nothing is appended — with the script's
  own folder as working directory and stdin closed (`input()` fails at once
  instead of hanging).
- Success is exit code 0. A non-zero exit fails the run with the script name,
  step and the tail of its error output.
- The run waits until the script and every process it started have finished
  (owner decision), bounded by the Flow's time limit (default 60 minutes, at
  most 1440). A timeout or Stop ends the script and everything it started.
- No run folder, output file, SQL insertion or email is involved.
- Environment: the worker environment, without stale `METRONOME_FLOW_OUTPUT`,
  `_INPUT`, `_INPUTS`, `_RESULTS_DIR` and `_OUTPUT_FORMAT`; plus
  `METRONOME_FLOW_MODE=run`, `_NAME`, `_RUN_ID`, `_STEP`, `_STEPS`, `_VALUE`,
  `PYTHONUNBUFFERED=1` (live output) and `PYTHONIOENCODING=utf-8` (Unicode
  prints cannot fail on a code-page pipe).

## Interpreter

`flow_python.resolve_interpreter` picks, in order:

1. the Flow's **Python to use** path;
2. `DG_PYTHON_EXE` (environment or `.env`);
3. `py` on `PATH`;
4. `C:\Windows\py.exe`;
5. `%LOCALAPPDATA%\Programs\Python\Launcher\py.exe`;
6. `python` on `PATH`, skipping the Microsoft Store stub under `WindowsApps`.

A configured path that does not exist, or no interpreter at all, fails closed
with a clear message. Metronome never falls back to its embedded runtime. The
run's first event names the interpreter and why it was chosen.

## Storage and API

- Additive columns on `flows`: `python_run_mode` (NULL or `outputs` = today,
  `run`), `python_interpreter`, `python_timeout_minutes`.
- `FlowWrite` validates them: interpreter optional, absolute, not a `.py` file,
  no wildcards or control characters; time limit 1–1440. In run mode SQL,
  email and transformation are forced off and the NOT NULL output columns get
  inert values; the `{value}`/`{index}` filename rule does not apply. The mode
  can change on edit.
- `job_section` adds `mode`, `interpreter` and `timeout_seconds`; the claim
  gate requires the worker capability `python_script_run_v1` for run-mode jobs.
- `POST /api/flows/python/inspect {path, interpreter}`: a soft builder check —
  whether the service account can read the script, a hint when its drive letter
  is not visible to the service (mapped drives are per sign-in; use
  `\\server\share\…`), and which interpreter will be used. It never executes
  anything.

## Runner

- `flow_python.run_process` replaces `subprocess.run` for both modes: `Popen`
  with piped output read by two threads (line splitting with terminal `\r`
  semantics, UTF-8 then locale decoding, 2000-character line cap, tails kept),
  a one-second loop for time limit, observer ticks and stop requests, and a
  whole-tree kill (`taskkill /T /F` or `os.killpg`, precedent
  `flow_recorder_worker._close_recorder`).
- Run mode finishes when the main process has exited, no tracked descendant is
  alive (`conhost.exe` ignored) and the output pipes reached end-of-file.
  Outputs mode keeps today's semantics (the step ends with the main script;
  leftovers are reported).
- New worker-only `app/flow_process_tree.py` (`psutil`, optional):
  `ProcessTreeObserver` samples descendants every second, keys them by
  (pid, create time) against PID reuse, keeps orphans, reports label, times,
  CPU, memory and best-effort exit codes, redacts secrets in command lines, and
  kills the whole tracked set. Without `psutil` it degrades to the root process
  and pipe end-of-file. The worker injects it through
  `execute_flow(..., script_observer_factory=...)`, so portable `run_flow.py`
  files never import `psutil`.
- Dependency: `psutil==7.2.2` in `requirements.txt`, `requirements-ci.txt` and
  `requirements-ci.lock`.

## Worker and other readers

- `execute_python_job` branches to `execute_python_run_job`: events
  `python_scripts` (mode and interpreter), `python_step`,
  `python_step_complete`/`python_step_failed`, `python_waiting` ("run_all.py
  exited 0; waiting for 2 processes it started") and `python_complete` (results
  capped at 20 entries and 1000-character tails). No artifacts and no `no_op`,
  so the last-success time updates; publish and SQL are skipped.
- Success message: "Ran run_all.py (exit 0) in 4m 12s; 3 other processes
  finished."
- `flow_activity.row_progress`: run mode shows Prepare run → Run scripts
  (total from `run_count`, completed from `python_step_complete` events) →
  Finish. Outputs mode is unchanged.
- Failure-alert and email source labels, the Flows list type ("Runs N
  script(s)", destination "—"), and standalone dry-run (mode and interpreter)
  learn the mode. Groups, folder rename, resume refusal and the Gemini client
  are unchanged.

## Builder

- A choice at the top of **Python scripts**: Just run the scripts / Produce a
  file or SQL table.
- Run mode hides the Output and Email steps and the upload-based Browse (an
  upload copies one file without its neighbours); help explains pasting the
  full path (Explorer: Copy as path). It adds **Python to use (optional)**,
  showing the interpreter the inspect endpoint resolves, and **Stop a script
  after (minutes)**. One line states that a run ends when the script and
  everything it started have finished.

## Tests

- Write validation, forced defaults, round-trip, `job_section`, claim gate (an
  adapter-only worker never claims a run-mode job).
- Worker: exact argv without `--output`; working directory; sibling import
  (Flow interpreter set to the test interpreter); environment present and
  output variables absent; `input()` fails fast; non-zero exit fails with its
  tail; timeout kills parent, child and grandchild; a blocking launcher with
  two children succeeds; a fire-and-forget child is waited for and its output
  captured; degraded mode without `psutil`.
- Interpreter resolution order and fail-closed messages; inspect endpoint's
  mapped-drive hint.
- Existing outputs-mode tests pass unchanged through the new runner.
- `tests/test_flow_activity.py` phases, `tests/test_flow_builder_contract.mjs`
  run-mode payload and markup, and the preview walkthrough.

## Rollback

Pause run-mode schedules first, then revert (see [the README](00_README.md)).
