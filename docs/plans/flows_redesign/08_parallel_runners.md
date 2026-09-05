# Plan 8 — Capacity then per-flow download fan-out

## Goal
One headed plus up to five headless slots; complete-bundle downstream processing.
Deliver phases A and B as separate merges.

## Current state
claim_run checks mode/adapter/store, not ordinary worker_id pinning. Scans consume
browsers. execute_job owns the ordered task matrix and whole-run ordinals.
Publication/SQL happen later in shared execute_flow; queued runs hold resource reservations.

## Phase A
- Capacity 1..5, default 1; headed always 1. Count claimed/running runs AND scans
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
  Default per-flow parallelism 1; explicit 1..5, headed fixed 1.
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

## Migration and rollback
Additive schema, default 1, capability-gated jobs. Drain task runs before reverting
B; never let an old worker reinterpret them. Retain history and files. Lowering
capacity does not terminate active work.

## Acceptance criteria
Independent connections test claim races, scans versus runs, headed limit,
throttling, old recovery and exact slot stops. B tests stale/duplicate reports,
finalizer races/crash, filename/role integrity, cancellation, resume pins and
full-bundle SQL gating. Service/SSO/live portal checks are operational verification.
