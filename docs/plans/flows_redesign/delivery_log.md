# Flows redesign delivery log

## Reviewed plans — merged
- PR #52: https://github.com/datap0nd/data_governance/pull/52
- Merge: 712ac01d. Source baseline: c527be4b.
- Reviewed all eight plans together; corrected contracts and rollback sequence.
- Validation: documentation diff check; Ubuntu CI passed.

## Plan 1 — Paths
- PR #53: https://github.com/datap0nd/data_governance/pull/53
- Merged as 812c6161. Ubuntu CI passed; Windows CI was still pending when checked.
- System > Paths, root diagnostics, staged enforcement, server/worker preflight,
  source-junction checks and unique contained upload staging.
- Existing schedules keep their paths until explicit enforcement/adoption.
- Eight focused path tests and seven existing installer tests pass. All 14
  frontend .mjs suites and JavaScript syntax check pass.
- Local test database: opened Paths, checked impact, saved/reloaded root and
  visually verified controls. No appliance settings were changed.
- Full Python suite: 1,325 passed; eight focused path tests passed again after
  final scheduler, upload and junction checks. JavaScript syntax/display pass.
- Local pytest needs its best-effort current-directory symlink helper disabled
  because Windows cannot resolve those links. Application symlink tests still run.

## Plan 2 — Folders
- PR #54: https://github.com/datap0nd/data_governance/pull/54
- Merged as 47305001.
- Automatic managed folders for new builder flows, stable physical paths on
  rename, preserved files on delete, explicit legacy adoption and compensation
  for empty allocations after I/O/database failure.
- 44 affected tests and all 15 frontend suites pass. Local browser created a
  paused Local flow without a target input and showed the saved owned folder.
- Full suite: 1,335 passed; final nine layout tests passed, including database
  failure compensation after allocation. No existing tests were weakened.

## Plan 3 — Layout and shared storage
- PR #55: https://github.com/datap0nd/data_governance/pull/55
- Merged as 61beffdc; Ubuntu CI passed.
- Ownership-checked explicit repair, immutable script copies on managed saves,
  shared storage for new managed jobs and legacy recovery identities preserved.
- New coverage executes a real Local acquisition into the shared store, checks
  run-folder registration, capability negotiation, script version preservation,
  foreign/link repair refusal and cross-profile store identity.
- Full regression: 1,341 passed; one new test inspected the raw snapshot
  instead of the normalized artifact and was corrected. All seven final shared
  layout tests pass, including that case; 24 layout/publication tests passed.
- Local browser repair succeeded on a paused managed Local flow.
- All 15 frontend suites and JavaScript syntax pass.

## Plan 5 — Grouped list and folder access
- Fixed source groups, persisted expansion, active/failure counts, shared row
  semantics, preserved Run/Stop/Active controls and accessible overflow actions.
- Folder action opens only a saved, checked path in a verified local interactive
  session; remote, proxied and session-0 requests return a copyable path.
- 32 affected Python tests and all 16 frontend suites pass; syntax passes.
- Browser verified collapsed defaults, expansion/focus and persistence on reload.
  Local private output and exact SQL identifier case have regression coverage.

## Remaining sequence
6 Sorting → 7 Builder → 4 Standalone →
8A Capacity → 8B Fan-out. Each has its own tested PR and merge boundary.

## Operational verification
Actual appliance SSO, service installation, visible Explorer and real SQL are
not established by local synthetic tests. No historic files or stores are moved.
Revert dependent merges in reverse order; Git cannot undo delivered data/SQL.
