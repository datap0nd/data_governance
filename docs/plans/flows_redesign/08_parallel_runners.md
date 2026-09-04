# Plan 8 — Parallel runners: 1 headed + up to 5 headless, per-flow fan-out

## Goal

Run several flows at once on the BI desktop (at most one headed run and
up to five headless runs), and split a single flow's multiple downloads
(export views × periods, or dashboard links × periods) across those
runners, while keeping today's guarantees: one active run per flow, SQL
only after the complete bundle validates, retention and publish
serialized per folder.

## Current state (main @ c527be4)

- Exactly two worker processes exist: the `MXFlowsWorker` service
  (`bi-desktop-headless`) and the `Metronome_Flows_Headed` task
  (`bi-desktop-headed`) (`setup.ps1:492-521`). Each holds one Edge
  persistent context and one page, claims one run at a time, and runs
  everything inline on the loop thread (`run_worker`,
  `app/flow_worker.py:6864-7016`).
- Routing is by `capabilities.headed` (`claim_run`,
  `app/routers/flows.py:4430-4548`, `worker_can_claim` `:4495-4508`);
  `_build_job` pins a single `execution.worker_id` constant (`:1486`).
- Limits: one active run per flow (`:1601-1607`), per SQL target and per
  publish folder locks (`app/routers/pipelines.py:435-495`), one
  `current_run_id` per worker, one worker per profile directory (OS lock
  `flow_worker.py:148-181`). No global cap exists.
- Inside a run, `execute_job` builds `tasks = export_views × periods`
  (`:6224-6234`) and walks them sequentially on the shared page, staging
  dir, run folder, artifacts list, and resume keys (`_export_task_key`
  `:5696`). HTTP replay (`:6541-6647`) is the only transport that does
  not need the page.
- `artifact_store_id` is per profile directory (`app/flow_publish.py:40`),
  so SQL Retry / Resume are pinned to the worker that produced the run.
  Plan 3 moves the store under the flows root and makes the id
  per-machine; this plan depends on that.
- Launch/stop helpers know one service and one task
  (`app/flow_local_runner.py`); `ensure_local_worker` inspects one worker
  id (`flows.py:3106`); `_worker_readiness` expects one worker per mode
  (`pipelines.py:252-268`).
- Stale detection and heartbeat are per run (`fail_stale_runs` `:364`,
  heartbeat 30 s `flow_worker.py:6999`).

## Design

Two phases. Phase A gives multi-flow parallelism with the existing
run-level protocol. Phase B adds run tasks so one flow's downloads spread
across workers. Phase A is independently shippable.

### Phase A — worker pool and capacity

**Capacity settings** (`app_settings`, editable from a new **System >
Runners** panel next to Paths, or a section on the Paths page):

- `flows_headless_runners` (1..5, default 2)
- `flows_headed_runners` fixed at 1 (not editable; shown read-only)

**Workers**: headless services `MXFlowsWorker1..N` with ids
`bi-desktop-headless-1..N`, each with its own profile
`%USERPROFILE%\.metronome-flow-browser-<n>`, its own downloads staging,
and its own SSO bootstrap (setup loops over N like it already loops over
the two profiles at `setup.ps1:595-610`). The headed task stays single
(`Metronome_Flows_Headed`, `bi-desktop-headed`). `setup.ps1` reads N from
`DG_FLOWS_HEADLESS_RUNNERS` (written by the settings save) and installs
or removes services to match; the app cannot install services itself, so
the Runners panel shows "N configured · M installed · rerun setup to
apply" when they differ.

**Worker identity**: `register_worker` gains `pool: "headless"|"headed"`
and `slot: n` in capabilities. `flow_local_runner`:

- `launch_local_worker(mode, slot=None)` → `sc start MXFlowsWorker<slot>`
  (all slots when `slot is None`), headed unchanged.
- `stop_local_worker(mode, process_id, slot)` → PID first (already exact),
  else the specific service.
- `ensure_local_worker()` → starts every installed headless service that
  is not online.

**Claim with global caps** (`claim_run`): before matching queued runs,
count active runs (`claimed`/`running`) whose job `execution.browser_mode`
equals the worker's mode; if `headless` count ≥ `flows_headless_runners`
or `headed` count ≥ 1, return `{"run": None}` with `reason: "capacity"`.
The per-worker `current_run_id` rule remains, so the cap is effectively
the number of installed workers; the setting exists so the owner can
throttle below the installed count without uninstalling services. Keep
the atomic `UPDATE … WHERE status='queued'` claim; add
`BEGIN IMMEDIATE` around the count + claim so two workers cannot both see
capacity 4/5 and both claim.

**Job pinning**: `_build_job` stops emitting a single `execution.worker_id`;
it emits `execution.pool`. `queue_flow_run` launches the pool, not a
worker.

**Staleness**: unchanged (per run heartbeat).

**Readiness**: `_worker_readiness` reports per pool
(`online`, `installed`, `configured`).

**Artifact store**: plan 3's shared store under `<flows_root>\.metronome\artifacts`
so any headless slot can Resume/Retry a run produced by another slot.
Replay recipes (`flow_replay.RECIPES_FILENAME` in the profile) move to
`<flows_root>\.metronome\replay\recipes.json` with a file lock
(`msvcrt.locking`/`fcntl.flock` like the worker lock) so slots share the
warm cache. Credentials stay per account (all slots run as the same
account).

**Folder and target locks**: unchanged. With per-flow folders (plan 2)
the publish lock is per flow; the SQL target lock still prevents two
flows from writing one table concurrently.

### Phase B — per-flow download fan-out

**New table**

```sql
CREATE TABLE IF NOT EXISTS flow_run_tasks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
  task_key      TEXT NOT NULL,            -- _export_task_key(export_view, period)
  ordinal       INTEGER NOT NULL,         -- 1..N, drives filename {index} and progress "3 of 8"
  status        TEXT NOT NULL DEFAULT 'queued',   -- queued|claimed|running|succeeded|failed|cancelled
  worker_id     TEXT,
  attempt       INTEGER NOT NULL DEFAULT 0,
  target_filename TEXT NOT NULL,          -- server-rendered, unique within the run (no " (2)" races)
  artifact_json TEXT,
  error         TEXT,
  claimed_at, started_at, finished_at, heartbeat_at DATETIME,
  UNIQUE(run_id, task_key)
);
```

**Run lifecycle with tasks**

1. `queue_flow_run*` inserts the run as today **and** its tasks
   (`_build_job` already computes the matrix; move that into
   `flow_jobs.task_matrix(job)` shared with the worker). Runs with one
   task, Outlook runs, and file runs get one task and behave exactly as
   today.
2. The run gets `parallelism` = `min(len(tasks), flow.max_parallel_downloads, pool_capacity)`
   where `flows.max_parallel_downloads` is a new column (default 1 for
   headed, 3 for headless; editable in the builder's "What to download"
   step). Headed runs are always 1 (one visible browser).
3. **Claim** becomes task-based: a worker asks for work; the server
   returns `{run, task}` where `task` is the next queued task of a run
   whose `active task count < parallelism`, preferring runs already
   started (finish what is in flight before opening a new run) then FIFO.
   A worker holds at most one task. `flow_runs.status` becomes `running`
   when its first task starts. The legacy `{run}` response stays for
   old workers during rollout (`schema_version` gate in the job).
4. Each task executes `execute_task(job, task, …)`: open the portal on
   this worker's page, download the one export, store it into the run
   folder under `Downloads` using `task.target_filename` (created
   exclusively; a collision is a hard failure, not a `(2)` suffix,
   because the server owns names), report progress with
   `task_key`/`ordinal`, write `artifact_json` on the task.
5. **Finalize**: when the last task succeeds, the server marks the run
   `finalizing` and the *next* worker of the same pool to claim gets a
   `finalize` task: it validates the complete bundle from
   `flow_run_tasks.artifact_json` (same checks as `_publish_direct_artifacts`
   `flow_worker.py:6763`), publishes (direct mode), runs the
   transformation, loads SQL, and completes the run. This keeps the
   invariant that transform + SQL happen once, after everything is
   downloaded. Any failed task → run `failed` after the other in-flight
   tasks finish (they are not killed; their artifacts stay resumable).
6. **Resume**: `_resume_completed_keys` maps 1:1 onto succeeded tasks;
   resuming re-queues only failed/cancelled tasks.
7. **Stop**: cancels queued tasks, then stops the workers holding
   running tasks (existing PID-exact stop), marks the run cancelled.
8. **Heartbeat/stale**: per task (worker heartbeats carry `task_id`);
   `fail_stale_runs` fails stale tasks, and a run with a stale task
   follows rule 5.

**Portal considerations**

- Each worker has its own SSO session; ASAP and GSCM tolerate several
  sessions per account today (headed + headless already coexist). Add a
  pool-wide setting `flows_max_sessions_per_portal` (default 3) enforced
  in claim so a portal is never hit by more than N tasks at once.
- Replay: with the shared recipe file, parallel tasks of a bundle can
  replay concurrently; browser fallback remains per task.

**Progress and UI**

- `progress_json` gains `tasks: {total, queued, running, succeeded, failed}`;
  the list (plan 5) shows "3 of 8 downloading on 2 runners"; the run log
  page groups events by task.
- The Runners panel shows each slot: online, current run/task, last
  error, profile path, SSO state.

**Standalone script** (plan 4) runs tasks serially with parallelism 1;
no change.

## Step-by-step

Phase A
1. Settings keys + Runners panel (`renderRunners` block, `.mjs` test).
2. `flow_local_runner` slot-aware launch/stop/ensure; `setup.ps1` loop
   installing `MXFlowsWorker<n>` services and profiles; tests mirroring
   `test_setup_installs_headless_flow_worker_service` and
   `test_stop_worker_targets_exact_registered_process` for slots.
3. `register_worker` pool/slot; `claim_run` capacity check in
   `BEGIN IMMEDIATE`; `_build_job` pool instead of worker id; readiness
   per pool. Tests: `test_claim_respects_headless_capacity`,
   `test_second_headed_run_waits`, `test_two_headless_workers_run_two_flows`,
   `test_capacity_setting_below_installed_count_throttles`.
4. Shared replay cache with lock (`tests/test_flow_replay.py` addition).
5. Docs: README worker paragraph, `docs/flow_runners.md`.

Phase B
6. Migration `flow_run_tasks`, `flows.max_parallel_downloads`;
   `flow_jobs.task_matrix`; server-rendered `target_filename` (move
   `_render_filename` to `flow_jobs` so server and worker share it).
7. Task-based claim/progress/heartbeat/finalize endpoints (versioned
   under the same `/worker/{id}/…` prefix with `task_id`), legacy path
   kept behind `schema_version`.
8. Worker: `execute_task` factored out of `execute_job` (which becomes
   "run all tasks then finalize" for legacy/standalone use), `finalize`
   handler, per-task staging subfolder `<profile>\downloads\<task_id>`
   so `_edge_completed_download` cannot cross-assign files.
9. Resume/Stop/stale per task; UI progress; run log grouping.
10. Tests: `test_run_with_three_tasks_is_spread_across_two_workers`,
    `test_finalize_runs_once_after_last_task`,
    `test_failed_task_fails_run_after_inflight_tasks_finish`,
    `test_resume_requeues_only_failed_tasks`,
    `test_server_assigned_filenames_are_unique_and_exclusive`,
    `test_headed_runs_never_exceed_parallelism_one`,
    plus `test_flow_worker_discovery.py` coverage that a task uses its own
    staging subfolder.

## Risks

- **Portal rate limits / account lockout** from several concurrent SSO
  sessions. Mitigated by `flows_max_sessions_per_portal` and by
  defaulting headless parallelism to 2 slots; raise after observing.
- **Disk and memory**: five Edge profiles ≈ 5× today's footprint.
  Runners panel shows per-slot disk use; setup warns under 10 GB free.
- **Rollout**: Phase A needs a setup rerun to install services; until
  then the single service keeps working with capacity 1.
- **Finalizer hand-off** adds one claim round-trip (≤10 s idle poll).
  Acceptable; can be shortened by letting the last task's worker
  finalize immediately when it is the only in-flight worker.

## Acceptance criteria

- Two headless flows run at the same time on two slots; a third waits
  when capacity is 2; a headed run never overlaps another headed run.
- A flow with eight export views on three headless slots completes with
  eight uniquely named files, one transformation pass, one SQL
  transaction, and a run log that shows which slot did what.
- Stop, Resume, SQL Retry, retention, and Direct-file publish behave as
  today for single-task runs (existing tests unchanged and green).
