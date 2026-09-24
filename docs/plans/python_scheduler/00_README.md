# Python scheduler, `.env` credentials and script monitoring — plan set

Written against `main` `a3a3b62` on 2026-09-24. This set holds plans only; each
stage is delivered by its own tested PR, recorded in
[delivery_log.md](delivery_log.md).

## Owner request

1. Flow SQL writes use the `DG_UPLOAD_PGUSER` / `DG_UPLOAD_PGPASSWORD`
   credentials. Read them from a `.env` file, with placeholders ready to fill in.
2. Add a literal "just run this script" option to Python Flows, so Metronome is
   also a plain Python scheduler — for example a script that only starts other
   scripts.
3. Monitor scripts as closely as possible: the stage a script is in, the other
   scripts it runs, proper monitoring and run history.

## Owner decisions (2026-09-24)

- "Just run it" uses the computer's Python (the Windows `py` launcher, the same
  interpreter a double-click uses). A Flow may name a specific `python.exe`,
  for example a virtual environment.
- When the main script exits but processes it started are still running, the
  run waits for them; the time limit still applies.
- No failing script types were observed yet; the owner has not tried scripts.

## Findings behind the plan

- `app/config.py` reads everything from `os.environ` at import; no `.env`
  parser exists. On the work PC the upload credentials can only come from
  machine-level Windows variables: no service definition in `setup.ps1` sets
  them.
- `flow_python.run_scripts` always appends `--output` (and `--input` from the
  second script on) and fails when that file is missing or empty, so a
  launcher script fails (argparse exit 2, or "did not create --output").
- The worker runs scripts with its own interpreter, the embedded Python 3.13
  in isolated mode (`._pth`): the script folder is not on `sys.path`,
  `PYTHON*` variables are ignored and only Metronome's packages exist.
- Output is captured only after a script exits (last 4000 characters). No PID
  or child process is recorded; the timeout kills only the direct child; Stop
  kills the whole worker (Windows only) and orphaned children survive.

## Delivery order

Merge one tested PR at a time, each pinned to its tested head with its own
release package under `docs/testing/releases/`:

1. Plans (this set).
2. [Stage 1: `.env` credentials](01_env_credentials.md) — no journey change.
3. Preview checkpoint — a clickable fictional preview of the run-mode builder,
   the run log's Script activity section and the filtered run history; the
   owner's feedback is the only planned pause ([DESIGN.md](../../../DESIGN.md)).
4. [Stage 2: "Just run it" mode](02_run_mode.md), as approved.
5. [Stage 3: script monitoring and run history](03_script_monitoring.md), as
   approved.

## For the implementing agent

- Follow [AGENTS.md](../../../AGENTS.md) (delivery, verification, invariants),
  [DESIGN.md](../../../DESIGN.md) (UI and the preview pause) and
  [the testing workflow](../../testing/README.md) (release packages).
- Deliver one stage per PR, in the order above, each branched from current
  `origin/main`, verified with the smallest affected `tools/check.py` set, and
  merged pinned to its tested head after `Merge ready`. Append each merge to
  [delivery_log.md](delivery_log.md).
- Before any Stage 2 or Stage 3 UI code, build the fictional preview
  `app/static/recording-preview/python-scheduler.html` / `.js` (the
  `python-scripts.html` harness pattern) with a Playwright walkthrough at
  1280×900 and 390×844, and stop for the owner's feedback.
- Line numbers in these plans refer to `a3a3b62` and are approximate; re-read
  the code before editing. Names such as `resolve_interpreter` or
  `python_script_live_v1` are proposals; keep them consistent across stages
  once chosen.

## Shared rules

- Additive migrations and versioned worker capabilities
  (`python_script_run_v1`, `python_script_live_v1`): an older worker never
  claims work it cannot honour.
- `app/flow_python.py` stays standard-library only, because it is copied into
  every portable `run_flow.py`. Process tracking (`psutil`) lives in
  worker-only modules that the worker injects.
- Credentials are never committed, logged or shown: `.env` is ignored by git,
  diagnostics carry variable names only, and command lines and console output
  are redacted.
- Scheduled execution fails closed: a missing script or interpreter stops the
  run with a clear message; Metronome never substitutes another interpreter.
- Stored timestamps stay UTC; the UI shows Dubai time.
- Work-PC, live portal and hardware checks stay opt-in.

## Rollback

Revert stage PRs in reverse order. Before reverting Stage 2, pause the
schedules of run-mode Flows: older code would treat them as file-producing
Flows, run each script once with `--output` and then fail. Stage 1's `.env`
file stays on disk and is ignored by older code; move its values back to
Windows variables first if Stage 1 is reverted. Console output rows and the
additive columns are inert for older code.
