# Flows redesign delivery log

## Reviewed plans — merged
- PR #52: https://github.com/datap0nd/data_governance/pull/52
- Merge: 712ac01d. Source baseline: c527be4b.
- Reviewed all eight plans together; corrected contracts and rollback sequence.
- Validation: documentation diff check; Ubuntu CI passed.

## Plan 1 — Paths
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

## Remaining sequence
2 Folders → 3 Layout → 5 List → 6 Sorting → 7 Builder → 4 Standalone →
8A Capacity → 8B Fan-out. Each has its own tested PR and merge boundary.

## Operational verification
Actual appliance SSO, service installation, visible Explorer and real SQL are
not established by local synthetic tests. No historic files or stores are moved.
Revert dependent merges in reverse order; Git cannot undo delivered data/SQL.
