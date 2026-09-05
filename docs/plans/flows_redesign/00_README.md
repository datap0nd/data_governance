# Metronome Flows redesign — reviewed plan set

Reviewed against main c527be4b on 2026-09-05. The source branch contains plans
only. This revision corrects migration, recovery, concurrency and UI contracts.

## Delivery and rollback
Merge one tested PR at a time: reviewed plans → 1 Paths → 2 Folders → 3 Layout
→ 5 List → 6 Sorting → 7 Builder → 4 Standalone → 8A Capacity → 8B Fan-out.
Record merges and validation in delivery_log.md. Use additive migrations and
versioned job capabilities. Revert dependent merges in reverse order.
Git cannot undo already published files, SQL commits or operator path changes.
Never restore an old database over new run history or delete historic artifacts.

## Shared decisions
- Root: app setting, then DG_FLOWS_ROOT, then DB parent/metronome/flows.
  Source folders: ASAP, GSCM, Outlook, Local, Web.
- Staged enforcement: configuring a root does not disable existing schedules.
  An explicit enforcement setting governs legacy paths; new managed flows
  always validate their managed destination. Show affected paths before enabling.
- External Local inputs remain read-only when enforcement is off. With it on,
  inputs must be under Local. Preserve private snapshot and unchanged-file rules.
- Stable ID-suffixed folders: display-name edits update manifests, never paths.
- Downloads and Scripts are siblings. Local snapshots remain private.
- New managed private artifacts use shared root storage; historic profile
  stores and exact absolute recovery paths/identities remain untouched.
- Configured-path containment is not an OS sandbox for transformation scripts.
- Standalone uses installed code/runtime and caller credentials; never stores
  secrets or assumes a server outage means no competing worker.
- Keep cross-flow publish and SQL locks: legacy folders and SQL targets can
  still be shared. Capacity includes scans, downloads and finalizers.

## Principal review findings
1. Automatic enforcement would stop existing schedules on upgrade.
2. normalize_target_path is a Windows identity helper, not realpath validation.
3. register_folder receives the run path; execute_ops receives the storage parent.
4. Filesystem rename and SQLite UPDATE cannot be made atomic by ordering them.
5. Moving stores leaves absolute artifact/retention references stale; adding
   migrated_from also breaks the current exact marker comparison.
6. execute_job requires a Playwright page. run_worker owns browser startup,
   publication, transformation and SQL orchestration.
7. _build_job depends on router source/SQL helpers, not just week calculations.
8. Ordinary claim routing checks mode/adapter/store, not the worker_id hint.
9. Catalog scans consume capacity; finalization must retain reservations.
   A finalizer lease cannot guarantee exactly-once SQL after an unknown commit.
10. Newest-first is descending, including aria-sort; nulls need explicit handling.

## Verification
Keep existing contract tests; do not weaken them to fit the proposal.
Execution changes require affected and full Python suites. UI changes require
all .mjs tests and node --check, including new tests in Windows/Ubuntu CI.
Record appliance SSO, service installation, interactive Explorer and live SQL
as operational checks, separate from synthetic test results.

