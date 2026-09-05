# Plan 1 — Paths settings and configured-path containment

## Goal
System > Paths configures root, diagnostics and explicit enforcement without
stopping existing schedules just because the app is upgraded.

## Current state
Verified: app/settings.py opens separate connections; app/path_safety.py only
classifies shares; flow_publish.normalize_target_path only normalizes Windows
identity. FlowWrite.target_folder is Optional but its validator requires it.
References: app/routers/flows.py:636,797,851,1464,2567,2617.

## Design
- app/flow_paths.py: source mapping, root precedence, native syntax/containment,
  status and job preflight. Reuse caller DB connections; no nested writer.
- flows_paths_enforced defaults false. Root alone does not enable enforcement.
- Reject relative/device paths, traversal, root equality, drive/share roots,
  controls, alternate streams, code-checkout descendants and private user targets.
  Resolve existing ancestors for junction/symlink escapes; do not use ntpath
  to perform POSIX filesystem I/O.
- Targets belong below the source directory; enforced Local sources below Local.
  Managed scripts narrow to Scripts in plan 3. Private URIs are not file paths.
- GET /api/system/paths returns effective/default root, enforcement, source
  directories and affected configurations without network disk probes on polls.
- PUT(root, create, enforced) validates and persists in one write transaction;
  refuse changes with queued/claimed/running jobs. Show migration impact first.
- Validate saves and every queue path, including scheduler/pipeline. Frozen jobs
  include policy when enforcement or managed layout applies. Check workers
  before portal/Outlook/Local/recovery/SQL-retry I/O and at write boundaries.
- Old policy-free jobs retain their compatibility contract, not a sandbox claim.
- Uploads use exclusive UUID directories under .metronome/uploads. Keep existing
  response fields. setup creates default directories without overriding settings.

## Step-by-step
Path module/tests → settings router/nav/UI → save/build/queue validation →
worker preflight → uploads/setup → docs/flow_paths.md and Local migration guide.

## Migration and rollout
Legacy paths remain until explicit enforcement/adoption. Plan 2 changes future
destinations without moving history. No automatic migration or schedule shutdown.

## Risks
Containment does not restrict arbitrary script process permissions. Browser
profiles, credentials and runtime temporary files have separate ownership.
Avoid remote stat calls in list polling; document TOCTOU limits.

## Acceptance criteria
Test native/Windows/UNC syntax, nonexistent tails, prefix tricks, links, private
paths, active-run conflict, queue/worker rejection, and unchanged legacy operation.

