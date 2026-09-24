# Python scheduler, `.env` credentials and script monitoring — plan set

Written against `main` `a3a3b62` on 2026-09-24. The owner wants the whole
change delivered **in one go**: the three parts below land together in one PR,
with one release package and one merge, recorded in
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
- Delivery in one go: "I want it done in one go please". All three parts ship
  in one PR, with no intermediate merges and no stop for preview feedback. The
  owner's instruction in the task wins over the preview pause in
  [DESIGN.md](../../../DESIGN.md); the fictional preview and its walkthrough are
  still built as usability evidence, without waiting for approval.

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

## Delivery

One PR delivers everything: this plan set plus

1. [Part 1: `.env` credentials](01_env_credentials.md);
2. [Part 2: "Just run it" mode](02_run_mode.md);
3. [Part 3: script monitoring and run history](03_script_monitoring.md).

Implement them in that order inside the same PR (Part 3 builds on Part 2's
runner), with one release package under `docs/testing/releases/` covering all
three, then merge once, pinned to the tested head, after the final-head
`Merge ready`.

## For the implementing agent

- Follow [AGENTS.md](../../../AGENTS.md) (delivery, verification, invariants),
  [DESIGN.md](../../../DESIGN.md) (UI rules and usability evidence) and
  [the testing workflow](../../testing/README.md) (release package).
- Push the implementation to this plan's PR (#143, branch
  `claude/upbeat-sagan-akzad6`) so plan, code, tests and evidence merge
  together. If that branch cannot be used, open one replacement PR from
  current `origin/main` that carries these plan files, and close #143.
- Extend the release package
  [`docs/testing/releases/2026-09-24-python-scheduler/`](../../testing/releases/2026-09-24-python-scheduler/test-plan.md)
  with the cases from each part's **Tests** section while implementing, and
  record actual results only.
- The owner waived the stop for preview feedback. Still build the fictional
  preview `app/static/recording-preview/python-scheduler.html` / `.js` (the
  `python-scripts.html` harness pattern) and walk it with Playwright at
  1280×900 and 390×844 as usability evidence; do not wait for approval.
- Verify locally with the smallest affected `tools/check.py` sets per part;
  final-head CI is the full regression.
- Line numbers in these plans refer to `a3a3b62` and are approximate; re-read
  the code before editing. Names such as `resolve_interpreter` are proposals;
  keep them consistent once chosen.
- Record the merge in [delivery_log.md](delivery_log.md).

## Shared rules

- Additive migrations and a versioned worker capability
  (`python_script_run_v1`, covering run mode, waiting, live output and
  cooperative Stop): an older worker never claims work it cannot honour.
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

Revert the PR. Before reverting, pause the schedules of run-mode Flows: older
code would treat them as file-producing Flows, run each script once with
`--output` and then fail. Move the `.env` credentials back into Windows
variables first; the `.env` file stays on disk and older code ignores it.
Console output rows and the additive columns are inert for older code.
