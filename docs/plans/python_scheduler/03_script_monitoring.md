# Stage 3 — Script monitoring and run history

Scope: live console output, process tree, stage markers, cooperative Stop and
run-history filters, for Python Flows in both modes. The run log and run
history change, so implementation follows the owner-approved preview.

## What the owner will see

- **Run log → Script activity** (Python runs): current script ("step i of n"),
  stage and progress bar, elapsed time, "waiting for N processes", and an amber
  "no output for N min" after 10 minutes of silence.
- **Process tree**: every process the script started — for example
  `run_all.py` → `load_sales.py`, `load_stock.py` — with PID, state (running,
  finished with exit code, stopped), start time (Dubai), duration, CPU and
  memory.
- **Live console**: stdout and stderr as they are written, stderr tagged,
  stage markers highlighted, follow toggle, Copy, Download .log and an
  "… N lines omitted …" divider when a run is very chatty.
- **Timeline events**: stage changes, child processes started and finished,
  waiting and Stop.
- **Flows list**: a running row shows the latest stage or child script.
- **Run history**: filter by Flow and status, "Show more", and exit code plus
  last stage for Python runs; a "Run history" item in the Flow row menu opens
  the filtered view.

## Stage markers (optional, any language)

Any line printed by the script or a child script:

- `::stage::Loading stock` sets the current stage;
- `::progress::3/10` or `::progress::45%` sets its progress.

Child scripts inherit the console, so their markers count too. Scripts without
markers still show which child script is running.

## Data and endpoints

- Table `flow_run_output(id, run_id, line_no, stream, text, pid, emitted_at)`,
  `UNIQUE(run_id, line_no)`; column `flow_runs.live_json` (latest process
  snapshot, stage, counters, main-exited and waiting state).
- `POST /api/flows/worker/{worker}/runs/{run}/live`: at most 1000 lines
  (`{line_no, stream, text ≤2000, pid, at}`), 300 processes, one stage, state
  and a dropped count. Same assignment check as the heartbeat; `INSERT OR
  IGNORE` makes retries idempotent; the reply carries `terminal`. A final
  snapshot after a cooperative Stop is accepted once, within 2 minutes.
- Per run the first 500 and last 4500 lines are kept; the gap is counted as
  omitted. When a run ends, console output of the same Flow's runs older than
  its newest 50 is deleted (records, events and summaries stay); deleting a
  Flow deletes its output.
- `GET /api/flows/runs/{id}/output?after_line=&limit=` is the incremental tail
  (the catalog-scan log pattern). `get_run` and `list_runs` return a parsed
  `live` object, never the raw column. `GET /api/flows/runs` gains `status` and
  `before_id`.

## Worker

- New worker-only `app/flow_script_live.py`: `LiveObserver` extends the Stage 2
  process observer. Reader threads never block: at most 5000 lines wait and
  extra lines are counted as dropped. It posts every 2 seconds (sooner at 500
  waiting lines), parses markers, and emits capped events through the existing
  progress endpoint: `python_stage` (200), `python_process_started` and
  `python_process_finished` (100 each, then one summary), `python_stopped`.
  Values of secret environment variables are masked in output lines.
- Capability `python_script_live_v1`.

## Stop

For a run-mode Python run on a worker with `python_script_live_v1`, Stop marks
the run cancelled as today but does not kill the worker. The worker sees the
terminal reply within about 2 seconds, ends every tracked process (orphans
included), posts the final snapshot and a `python_stopped` event, and keeps
serving. Every other run keeps today's Stop, which avoids a cooperative stop
racing an SQL insertion.

## Documentation

`docs/python_script_flows.md` gains a "Just run it" section: command, working
folder, interpreter order, environment, time limit, waiting and Stop, markers,
monitoring, retention and redaction ("never print secrets"), and limits —
mapped drives; programs opened with `os.startfile` or a GUI keep a run waiting
until the time limit; processes started through COM or the Task Scheduler are
not tracked; the service has no desktop; a long script occupies a headless
worker slot.

## Tests

- New `tests/test_flow_live_output.py`: assignment and terminal reply, bounds,
  idempotency, head/tail cap and omitted count, `live_json` merge, incremental
  read, parsed `live`, retention, Flow deletion.
- Worker integration: lines arrive while the script still runs; markers become
  stage events and live stage; child start/finish events; cooperative Stop
  ends the tree and the worker keeps serving; `\r` and non-UTF-8 output;
  redaction.
- `tests/test_flow_activity.py` stage message, new
  `tests/test_flow_run_log_live.mjs` (console append, omitted divider, follow),
  run-history filters and the preview walkthrough.
