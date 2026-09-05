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
- PR #56: https://github.com/datap0nd/data_governance/pull/56
- Merged as dd14b7ba.
- Fixed source groups, persisted expansion, active/failure counts, shared row
  semantics, preserved Run/Stop/Active controls and accessible overflow actions.
- Folder action opens only a saved, checked path in a verified local interactive
  session; remote, proxied and session-0 requests return a copyable path.
- 32 affected Python tests and all 16 frontend suites pass; syntax passes.
- Browser verified collapsed defaults, expansion/focus and persistence on reload.
  Local private output and exact SQL identifier case have regression coverage.

## Plan 6 — Sorting
- PR #57: https://github.com/datap0nd/data_governance/pull/57
- Merged as e55d72e8.
- Per-group stable sorting, native buttons with accurate aria-sort, session
  persistence and third-click reset. Null/invalid values stay last both ways.
- All eight keys are tested in both directions, including ties, invalid dates,
  immutability, unavailable/corrupt storage and exact API-order reset.
- All 17 frontend suites and syntax pass. Browser verified newest-first,
  three-click cycle, retained expansion/focus and wrapped paths/actions.

## Plan 7 — Stepped builder
- PR #58: https://github.com/datap0nd/data_governance/pull/58
- Merged as 8a2f37b8; Ubuntu CI passed.
- Numbered Source, What to download (portal), Where it goes, After download,
  Schedule/owner steps; native controls move existing DOM, preserving payloads.
- Create opens Source; edit collapses steps. Required-field errors reveal/focus
  the first invalid field; structured server errors map to their field.
- Local/Outlook get a summary rail. Managed replication clears folder identity;
  SQL refresh preserves the draft; filename examples are explicitly illustrative.
- Full Python suite: 1,346 passed. All 18 frontend suites and syntax pass.
- Browser verified Local hidden-field validation, Next/value retention, portal
  source switching, retained filters after server validation and summary layout.
- Broader CI for the list/sorting merges found old Stop/Delete markup contracts;
  this merge restores their original classes/markup. Those tests now pass in
  the full suite without weakening their assertions.

## Plan 4 — Standalone and shared execution
- Workers and generated launchers call the same execute_flow function for
  acquisition, publication, transformation, SQL and no-op handling.
- User clarification applied: all saved stages run by default, including SQL;
  --no-sql and --no-transform are explicit skips. Saved browser mode also applies.
- Versioned immutable bundles, safe generated source, read-only optional refresh,
  no-write redacted dry-run, explicit status/regeneration and managed-save generation.
- Shared process locks protect flow/output/SQL resources; offline execution uses
  a separate browser profile and local logs, without server history or retention.
- Full regression: 1,354 passed, one old indentation-sensitive source assertion
  failed after extraction. Replaced it with an AST/shared-state contract check;
  all 10 final standalone/failure-propagation tests pass. A runtime partial-download
  failure test checks that saved artifacts and failed timings survive in the log.
- All 18 frontend suites and syntax pass. Browser generated a launcher for the
  paused verification flow and confirmed current status and saved-stage defaults.

## Remaining sequence
8A Capacity → 8B Fan-out. Each has its own tested PR and merge boundary.

## Operational verification
Actual appliance SSO, service installation, visible Explorer and real SQL are
not established by local synthetic tests. No historic files or stores are moved.
Revert dependent merges in reverse order; Git cannot undo delivered data/SQL.
