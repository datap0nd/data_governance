# SQL table ownership: draft test plan

- Scope: use the assigned Flow owner's saved SQL username as the PostgreSQL
  target-table owner during the next successful SQL load, for append and replace.
- Baseline: `af94a0fdaa2566b138c825f2b35d80ea63afe677`.
- Preview revision: `8b4e342ad60b95043b8bef38566da1e34a515657`.
- Implementation status: awaiting owner feedback on the demonstrated journey;
  no production ownership implementation has been made at this cutoff.
- Related report: [test-report.md](test-report.md).
- Preview: run `python -m http.server 8768 --bind 127.0.0.1 --directory app`, then
  open `http://127.0.0.1:8768/static/recording-preview/sql-owner.html`.

## Prerequisites and fixtures

Use fictional CSV rows and a disposable PostgreSQL database/schema/roles.
Never run permission or rollback tests against business tables. CI integration
should use disposable PostgreSQL services; local unit tests must not discover
or connect to configured production SQL credentials. Any role setup in the
integration fixture belongs to the disposable test cluster only.

For work-PC verification, update both the app and SQL worker to the final
merged revision. Select an isolated target and an existing PostgreSQL role.
The uploading account needs ownership rights over the existing target, ability
to SET ROLE to the new owner, and inherited privileges of that owner so later
loads and dependent reads keep working. The new owner needs the schema
privileges PostgreSQL requires. Capture app/worker revision, UTC timestamp,
run ID and protected evidence references; never publish real CSVs, credentials,
private routes or raw database traces.

## Planned cases

| ID | Procedure | Expected result | Evidence |
| --- | --- | --- | --- |
| SO-01 | In Users, edit Maya's SQL username and save. Open the fictional Flow; save it without running. | The intended owner is shown; database owner does not change yet. | Browser assertion/screenshot at preview revision. |
| SO-02 | Choose Maya as Flow owner, save, then Run now with SQL enabled. | The SQL result reports the committed table owner; the fixture table owner changes. | Browser assertion; later real PostgreSQL catalog assertion. |
| SO-03 | Select missing-role, permission or CSV-error preview scenarios. Run; fix the scenario and retry. | Error beside the action explains the next step. Failure preserves rows and owner; retry succeeds. | Browser assertions; later transaction integration tests. |
| SO-04 | Clear the SQL username, choose a person without one, choose no owner, and disable SQL in separate attempts. | Existing ownership is unchanged; ordinary SQL behavior continues when enabled. | Browser/API/unit tests. |
| SO-05 | Change owner selection, navigate to Users and cancel editing, return to the Flow. Check desktop and 390px layout. | Unsaved owner choice survives; controls remain readable and reachable. | Browser screenshots and assertions. |
| SO-06 | Build jobs after assigning/changing/clearing/deleting a person's SQL identity; test pending recording settings using a different owner. Queue a job, then change the profile. | New jobs use the current selected person's identity. Queued jobs retain their frozen identity. Pending settings never reuse the old Flow owner's SQL username. | Job/API tests. |
| SO-07 | Attempt to claim ownership-bearing runs and recording validation scans using an older worker, then a capable worker. Also try an ordinary job without an owner. | Older workers cannot silently skip ownership; capable workers can claim; ordinary work retains compatibility. | Worker registration/claim tests. |
| SO-08 | In disposable PostgreSQL, replace a missing target, replace an existing target twice with schema expansion, and append rows. | Exact requested owner after commit; subsequent loads succeed; existing table identity, grants, constraints, indexes and dependent views are preserved. | PostgreSQL integration assertions. |
| SO-09 | Use exact mixed-case and quoted role identifiers, including punctuation. Inspect an unrelated table. | Identifier quoting selects only the intended role and target; no injected statement runs and unrelated ownership is unchanged. | PostgreSQL integration assertions. |
| SO-10 | Use a nonexistent role, missing schema privilege, no ownership rights, non-inherited membership or membership without SET permission. | Clear stage-specific failure and rollback; no rows/owner are changed and no role/grant is created automatically. | PostgreSQL integration assertions. |
| SO-11 | Force COPY/insert/ownership/commit failures and connection loss during commit. | Confirmed rollback preserves rows and ownership; ambiguous commit follows existing reconciliation rules and does not claim rollback or permit unsafe append retry. | Unit/integration failure tests. |
| SO-12 | Generate a portable recorded and legacy Flow, inspect frozen SQL target and run against the disposable database. | Portable and managed execution use the same ownership behavior; username changes make generated files stale through existing job hashing. | Portable tests. |
| SO-13 | On the work PC, set a valid SQL username in Users, assign that person to a disposable Flow, run, and refresh pgAdmin table Properties. Run again. | Actual database Owner matches the configured role; second SQL load still succeeds. | Protected pgAdmin screenshot and run IDs. |

## Planned checks and acceptance

Run affected SQL, People, worker, portable and browser suites; run the full
Python suite and affected frontend tests/syntax checks. Record exact commands,
revisions, versions, results and warnings when execution finishes. Require
disposable PostgreSQL integration and final-head Windows/Linux CI before merge.
Preview feedback must precede implementation of the changed journey.

The plan will be updated with exact test module commands as implementation
lands. No planned check is a passing result. Work-PC/pgAdmin checks remain
NOT RUN until actual evidence exists.

## Cleanup and rollback

Delete only designated disposable fixtures and stop their test servers. Keep
test Flow schedules disabled. Reverting application code does not undo a
previously committed PostgreSQL ownership transfer; restoring a real owner is
a separate explicit database action, performed by an authorized administrator.

## Design references

- [PostgreSQL ALTER TABLE ownership requirements](https://www.postgresql.org/docs/current/sql-altertable.html).
- [PostgreSQL inherited role privileges](https://www.postgresql.org/docs/current/functions-info.html).
- [PostgreSQL SET ROLE](https://www.postgresql.org/docs/current/sql-set-role.html).
