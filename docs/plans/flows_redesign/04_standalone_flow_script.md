# Plan 4 — Standalone runnable script copy per flow

## Goal

Every flow gets a Python script in its `Scripts\` folder that runs the
flow **without the Metronome server**: if the web app or the worker
service is down, an operator opens the flow folder, double-clicks or runs
`run_<slug>.py`, and gets the same download (and optionally the same
transformation and SQL load) into the same `Downloads` folder.

## What "runnable without Metronome" can mean here

The ASAP/GSCM drivers, Excel normalization, publish journal, retention,
and SQL loader live in `app/*.py` and total ~20k lines. Copying them into
each flow folder would be a fork that rots on the next update, and the
portal drivers change weekly (see `docs/agent_handoff.md`). So the script
is a **launcher over the installed code**, not a copy of it. The
dependencies that remain are all local to the BI desktop:

| Needed | Available with server down? | How the script gets it |
|---|---|---|
| Metronome code checkout (`$CodeDir`) | yes (files on disk) | recorded in the script header; overridable with `METRONOME_CODE_DIR` |
| Portable Python + Playwright + Edge | yes | the script re-executes itself with `<ProjectDir>\python313\python.exe` when started with another interpreter |
| Flow configuration (the job) | yes | frozen `flow_job.json` beside the script; `--refresh-from-db` rebuilds it from `governance.db` directly (`_build_job` needs only a sqlite connection) |
| Signed-in Edge profile | yes | uses the headless profile by default; if that profile's `.worker.lock` is held (worker service running) it uses `<profile>-standalone` and asks for a one-time SSO |
| DPAPI ASAP credential | yes, same Windows account | `flow_credentials.load_asap_credentials()` |
| `DG_UPLOAD_PG*` for SQL | not in a plain console | read from the NSSM service registry key (`HKLM\SYSTEM\CurrentControlSet\Services\MXFlowsWorker\Parameters\AppEnvironmentExtra`) when present, else from `<flows_root>\.metronome\standalone.env` (owner-created, documented), else `--no-sql` |
| Progress/retention/heartbeat from the server | no | replaced by local no-ops: print progress, `register_folder` returns `{"ops": []}` (no pruning), no heartbeat |

The script therefore keeps working when the **server** is down. It does
not work if the code checkout was deleted; that is documented in the
script header.

## Current state (main @ c527be4)

- No generation of scripts exists (`README.md:231`: Prefect script
  generation was removed). Precedent for reading `governance.db` without
  the server: `tools/get_flow_auth_url.py`.
- `execute_job(job, report_progress, profile_dir, *, run_id, register_folder, artifacts, download_staging_dir, …)`
  (`app/flow_worker.py:6173`) and `execute_outlook_job` (`:5957`),
  `execute_local_file_job` (`:6034`) take callables for every server touch
  point. `run_worker` (`:6864`) is the only place that posts to the API.
- The job dict is built server-side by `_build_job(db, flow_id)`
  (`app/routers/flows.py:1464`), which needs a `db` connection and the
  flow tables only.
- The profile lock (`_exclusive_worker_lock`, `flow_worker.py:148-181`)
  prevents two browsers on one profile.
- SQL config comes from `app.config` env vars (`app/flow_sql.py:16-22`).

## Design

### `app/flow_standalone.py` (new, importable, no FastAPI)

```python
def load_job(scripts_dir, *, refresh_from_db: str | None) -> dict
    # reads flow_job.json; with refresh_from_db=<governance.db path> opens sqlite directly,
    # calls app.routers.flows._build_job(conn, flow_id) (moved to app/flow_jobs.py so the
    # router is not imported), re-validates paths with flow_paths.

def resolve_runtime(job, *, profile_dir: str | None, headed: bool) -> Runtime
    # picks profile dir (default headless profile; fallback "-standalone" when locked),
    # code dir, python exe, SQL env (registry -> standalone.env -> none), staging dir.

def run(job, runtime, *, sql: bool, transform: bool, dry_run: bool, log) -> Result
    # allocates a local run id (negative timestamp-based, e.g. -20260904103012) so run folder
    # names stay unique and can never collide with server run ids; creates the run folder under
    # Downloads via flow_retention.create_run_folder; calls execute_job / execute_outlook_job /
    # execute_local_file_job with report_progress=log and register_folder=lambda p: {"ops": []};
    # runs _run_transformations and flow_sql.load_artifacts when enabled; writes
    # Scripts\runs\<timestamp>.json with the same shape as flow_runs.artifact_json + progress.

def render_script(flow, job, *, code_dir, python_exe) -> str      # the launcher text
def write_script_bundle(flow, job, scripts_dir, *, code_dir, python_exe) -> dict
    # writes run_<slug>.py, flow_job.json, README.txt atomically; returns paths + config sha
```

Server dependencies removed from `execute_job` by this plan: none. It
already takes callables. What must be added is a guard so a *local* run
never posts to the server by accident (`report_progress` is the only
channel and it is injected).

### The generated launcher (`Scripts\run_<slug>.py`)

Template (rendered with real values, ~80 lines):

```python
#!/usr/bin/env python3
"""Metronome standalone runner — Weekly sell-out (flow 12, ASAP).

Generated 2026-09-04 by Metronome c527be4 from flow configuration sha256 ab12…
Runs this flow without the Metronome server. Requires the Metronome code
checkout and Python runtime on this machine:
  code:   D:\\metronome\\data_governance
  python: D:\\metronome\\python313\\python.exe
Regenerated automatically whenever the flow is saved; edits here are lost.

Usage:
  run_weekly_sell_out.py                # download to ..\\Downloads, then transform + SQL if configured
  run_weekly_sell_out.py --no-sql       # skip the SQL handoff
  run_weekly_sell_out.py --headed       # show the browser
  run_weekly_sell_out.py --refresh-from-db   # rebuild the job from governance.db instead of flow_job.json
  run_weekly_sell_out.py --dry-run      # print the resolved job and runtime, do nothing
"""
import os, sys, subprocess
CODE_DIR = os.environ.get("METRONOME_CODE_DIR", r"D:\metronome\data_governance")
PYTHON   = os.environ.get("METRONOME_PYTHON",   r"D:\metronome\python313\python.exe")
if os.path.normcase(sys.executable) != os.path.normcase(PYTHON) and os.path.exists(PYTHON):
    sys.exit(subprocess.call([PYTHON, __file__, *sys.argv[1:]]))
sys.path.insert(0, CODE_DIR)
from app.flow_standalone import main
sys.exit(main(scripts_dir=os.path.dirname(os.path.abspath(__file__)), argv=sys.argv[1:]))
```

`flow_standalone.main` owns argparse so the launcher stays tiny and
stable across releases; behavior changes ship with the code, not the
generated file.

`README.txt` beside it explains, in operator language, when to use it,
that downloads land in `..\Downloads`, and that the server will **not**
know about the run (no run history, no failure email, no retention
pruning) until plan 4b below.

### When the bundle is (re)generated

- `create_flow`, `update_flow`, `adopt-folder`, `repair-layout`, and
  `PATCH /flows/{id}/enabled` (schedule flags are part of the job) call
  `flow_standalone.write_script_bundle`. Failure to write is a warning
  event, not a save failure (the folder may be on a share that is
  momentarily unavailable), and the list shows "Script out of date".
- `flow.json` gains `script: {path, generated_at, config_sha256, code_version}`;
  `_flow_out` exposes `script_status: "current" | "stale" | "missing"` by
  comparing the flow's current config hash with the manifest.
- `GET /api/flows/{id}/script` returns the rendered launcher and the job
  JSON (for a "View script" action in plan 5), and
  `POST /api/flows/{id}/script/regenerate` rewrites the bundle.
- Startup: a lifespan task regenerates bundles whose `code_version`
  differs from the running code, so an update never leaves scripts
  pointing at removed entry points.

### Security and secrets

- `flow_job.json` must contain **no secrets**. `_build_job` already omits
  credentials; the SQL block carries server/database/schema/table only.
  Add a test that greps the frozen job for `password`, `secret`,
  `cookie`, `authorization`.
- The DPAPI credential is only readable by the same Windows account; the
  script inherits exactly the worker's trust boundary.
- The script refuses to run when the frozen job's paths resolve outside
  the current flows root (plan 1 rule re-applied locally).

### Plan 4b (optional follow-up, not in scope): record local runs

When the server comes back, `flow_standalone` could insert a `flow_runs`
row with `trigger_type='standalone'` directly into `governance.db`
(sqlite allows it with the server down). Left out of this plan because
it doubles the state machine's writers; listed so reviewers can weigh it.

## Step-by-step

1. Move `_build_job` and its helpers (`_week_range`, `_week_window`,
   `_periods`, `_latest_discovered_week`) from `app/routers/flows.py` to a
   new `app/flow_jobs.py`; the router re-exports them so existing tests
   keep importing from `app.routers.flows`. Test: `tests/test_flows.py`
   job-shape tests unchanged and green.
2. `app/flow_standalone.py` with `load_job`, `resolve_runtime`, `run`,
   `render_script`, `write_script_bundle`, `main`.
3. Tests `tests/test_flow_standalone.py`:
   - `test_launcher_compiles_and_reexecs_with_portable_python`
     (`py_compile` the rendered text; monkeypatch `subprocess.call`).
   - `test_run_uses_local_noops_and_never_calls_httpx` (monkeypatch
     `httpx.Client` to raise; run a fake local-file job end to end with
     `execute_local_file_job`, assert artifacts under `Downloads`).
   - `test_locked_profile_falls_back_to_standalone_profile`.
   - `test_sql_env_resolution_order` (registry stub → env file → none).
   - `test_frozen_job_contains_no_secrets`.
   - `test_refresh_from_db_rebuilds_job_without_server`.
   - `test_bundle_regenerated_on_save_and_marked_stale_on_config_change`
     (in `tests/test_flows.py`).
4. Router: regenerate on save, `script_status`, `GET …/script`,
   `POST …/script/regenerate`; lifespan regeneration.
5. Worker: no change beyond plan 3, except exporting
   `execute_job`'s staging dir parameter so the standalone runner can use
   `<flows_root>\.metronome\staging\<pid>`.
6. Frontend (plan 5 owns the list): "View script" in the row menu and a
   "Script: current / stale" chip.
7. Docs: `docs/flow_standalone_runner.md` (operator guide: when, how,
   what is different from a scheduled run), README paragraph, and update
   `README.md:231` which says script generation was removed.

## Risks

- **Two browsers on one profile.** Covered by the lock fallback; the
  standalone profile needs a one-time SSO sign-in (headed). The README
  in the folder says so.
- **Divergence between frozen job and live config.** `script_status`
  plus `--refresh-from-db` cover it; the launcher prints which one it
  used.
- **Edge/Playwright version drift** is shared with the worker; no new
  risk.

## Acceptance criteria

- With `MXAnalytics` and `MXFlowsWorker` stopped, running the script for an
  ASAP, a GSCM, an Outlook, and a From-file flow produces the expected
  artifacts under the flow's `Downloads` and, with SQL enabled and env
  available, loads the table.
- The script is regenerated on every save and flagged stale when the
  saved configuration differs from the frozen job.
- The frozen job contains no credentials.
