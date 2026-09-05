# Plan 8 — Capacity then per-flow download fan-out

## Goal
Up to five headed and five headless slots with independent limits;
complete-bundle downstream processing. Phases A and B shipped separately.
The subsequent headed-parallel update replaces the original one-headed-slot
restriction at the owner's request, as its own reversible merge.

## Current state
claim_run checks mode/adapter/store, not ordinary worker_id pinning. Scans consume
browsers. execute_job owns the ordered task matrix and whole-run ordinals.
Publication/SQL happen later in shared execute_flow; queued runs hold resource reservations.

## Phase A
- Capacity 1..5 per browser mode, default 1. Count claimed/running runs AND scans
  within BEGIN IMMEDIATE and conditional claim. Return existing assignments first.
- Preserve MXFlowsWorker/bi-desktop-headless/profile as slot 1. Add slots 2..5
  with distinct services/profiles/staging; never remove existing services or copy
  live credentials automatically.
- Pool/slot metadata, exact stop/start, readiness and settings show configured
  versus online capacity. setup reads persisted setting or explicit argument.
- System > Flow workers saves the claim limit. Interactive setup installs new
  services with the account credential; unattended updates preserve existing
  credentials and report configured-but-uninstalled slots as offline.
- New managed shared store; old recovery remains producer-store checked.
- Keep replay caches per profile. Sharing mutable JSON is not needed for correctness.
- Update installer, unattended-update quiescing and all slot lifecycle checks.

## Phase B
- Canonical task_matrix preserves export/link/period order, task keys, whole-run
  ordinals, detected extensions and raw/normalized/original artifact roles.
- Durable tasks: run FK, key, ordinal, state, attempt, lease token/worker,
  timestamps/progress/artifacts/error; conditional claims fence stale reports.
- Negotiate task capability so old workers never run task jobs as whole runs.
  Default per-flow parallelism 1; explicit 1..5 in either browser mode.
- Task-local staging and immutable output; retain whole-run indices. Do not slice
  execute_job jobs if doing so resets ordinal and names.
- Parent stays running with finalizing stage until terminal, retaining all
  reservations. One conditional finalizer validates expected keys/counts,
  checksums/paths/names before publication, transformation and SQL.
- Unknown finalizer SQL commit outcome requires operator reconciliation, never
  automatic append retry or an unsupported exactly-once guarantee.
- Stop fences queued tasks/late reports, stops all exact workers and holds locks
  until workers stop or leases expire. Failure stops claims and drains in-flight.
- Resume retains only validated successes and source-run retention pins. SQL retry
  bypasses downloads. Local/Outlook no-ops and single-task behavior stay intact.
- Atomic global/per-run/per-portal caps include tasks, scans and finalizers.
  Actual portal concurrency tolerance remains unverified.
- UI/logs report completed/total tasks and actual active slots.

## Step-by-step
A: settings/atomic caps → add slots/lifecycle/readiness → tests/docs → merge.
B: matrix/capability schema → fenced lifecycle → worker task execution →
finalizer → cancellation/recovery → UI → race and full regression suites → merge.

## Headed-parallel follow-up
- Separate headed capacity and fixed interactive tasks/profiles for slots 1–5.
  Preserve headed slot 1's task, worker ID and profile for recovery.
- Setup registers five on-demand interactive tasks, quiesces all before update,
  and authenticates configured profiles separately. Never run visible browsers
  as service-session child processes or share a live profile.
- Launch the configured headed tasks for queued runs/scans/pipelines. Idle
  helpers stay alive during preparation and SSO, then exit after 60 idle seconds.
- Match helpers to the frozen browser mode and require a new headed protocol
  capability. Propagate headed authentication behavior into task execution.
- Count task workers against the right mode; retain cross-mode portal limits,
  one complete-bundle finalizer, exact-process Stop and existing recovery fences.
- Allow headed parallelism in the builder and expose visible slot status. Test
  headed claim races, concurrent download execution, mode isolation, old-worker
  rejection, reductions, task identities and stop targets before merging.

## Migration and rollback
Additive schema, default 1, capability-gated jobs. Drain task runs before reverting
B; never let an old worker reinterpret them. Retain history and files. Lowering
capacity does not terminate active work.

## Delivered behavior
The coordinator uses one pool slot and may execute one download itself; helpers
take free slots. Per-flow capacity defaults to 1 and per-portal capacity to 5,
always bounded by the configured global capacity. Task leases renew for 90
seconds; Stop uses exact process IDs and fences late reports. Parent progress
cannot erase a helper's committed artifact. A stopped parent remains reserved
until its coordinator and tasks are stopped or fenced by expiry.

Uncertain SQL marks the Flow as requiring reconciliation. Run, Resume and SQL
Retry reject it until an explicit acknowledgement after target reconciliation.
SQL Retry also rejects incomplete parallel bundles. Deleting a paused, terminal
Flow removes its task ledger together with run history and preserves files.
Standalone processing remains sequential and honors all saved stage flags.
See [operator details](../../flow_workers.md) and [delivery evidence](delivery_log.md).

## Acceptance criteria
Independent connections test claim races, scans versus runs, headed limit,
throttling, old recovery and exact slot stops. B tests stale/duplicate reports,
finalizer races/crash, filename/role integrity, cancellation, resume pins and
full-bundle SQL gating. Service/SSO/live portal checks are operational verification.
