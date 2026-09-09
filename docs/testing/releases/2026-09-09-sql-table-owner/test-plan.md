# SQL table ownership and binary recordings: test plan

- Scope: use the assigned Flow owner's saved SQL username as the PostgreSQL
  target-table owner during the next successful SQL load, for append and replace.
- Baseline: `af94a0fdaa2566b138c825f2b35d80ea63afe677`.
- Preview revision: `8b4e342ad60b95043b8bef38566da1e34a515657`.
- Owner approved the demonstrated journey and requested implementation/merge on
  2026-09-09. Final production code: `7574a0d43441eff3eef926fcb9590845ffd65b3a`.
- Download scope: preserve opaque binary/PDF bytes for recordings without table
  checks or processing, and recognize BOM-less UTF-16 tabular exports. True
  binary content remains unsupported for CSV processing/SQL insertion.
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

## Cases

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
| SO-12 | Generate portable recorded and legacy Flow source; inspect the frozen SQL target and embedded ownership helpers. Change the profile identity and regenerate. | Both embed the shared SQL implementation and frozen owner; generated source changes. Changing only the username does not invalidate browser recording configuration. | Portable source/hash tests; end-to-end SQL execution must be reported separately. |
| BD-01 | Record a fictional download with opaque bytes and an `.xlsx` or `.dat` filename, with table checks, transformation and SQL disabled. Finish recording and validate. | Saved bytes/checksum match the download; actual suffix is preserved, generic/absent suffix becomes `.bin`; unknown row count is not fabricated. | Storage regressions; actual Chrome portable download fixture. |
| BD-02 | Repeat with PDF, a sign-in HTML page and a broken Excel ZIP container. | PDF is preserved; sign-in/error HTML and corrupt recognized Excel still fail clearly. | Storage regressions. |
| BD-03 | Feed real binary to a recording requiring CSV normalization for checks/SQL. Then feed LE/BE UTF-16 tabular text without a BOM, including a surrogate at the detection boundary. | True binary fails without writing a fabricated CSV; UTF-16 rows/columns normalize correctly. | Storage regressions. |
| BD-04 | On the updated work PC, finish the same ASAP recording that reported unsupported binary; validate with its intended settings. Inspect the saved file and downstream processing if enabled. | Download-only recording preserves the original file. If SQL/checks require tabular contents, only decodable exports proceed; inspect an encrypted/protected file with authorized software if it is truly binary. | Protected run/session IDs, screenshot and checksum; no raw report upload. |
| SO-13 | On the work PC, set a valid SQL username in Users, assign that person to a disposable Flow, run, and refresh pgAdmin table Properties. Run again. | Actual database Owner matches the configured role; second SQL load still succeeds. | Protected pgAdmin screenshot and run IDs. |

## Checks and acceptance

Run affected SQL, People, worker, portable and browser suites; run the full
Python suite and affected frontend tests/syntax checks. Record exact commands,
revisions, versions, results and warnings when execution finishes. Require
disposable PostgreSQL integration and final-head Windows/Linux CI before merge.
Preview feedback must precede implementation of the changed journey.

Canonical checks (use the dependencies and browsers in the CI workflow):

```powershell
python -m pytest tests/test_sql_ownership.py tests/test_flow_sql.py tests/test_recorded_output_storage.py -q
python -m pytest tests/test_users_browser.py tests/test_flow_recordings.py -q
python -m pytest tests -q -ra
Get-ChildItem tests/*.mjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE) { throw "Node suite failed" } }
node --check app/static/app.js
node --check app/static/users.js
node --check app/static/flow_run_log.js
```

For the real PostgreSQL tests, configure `METRONOME_TEST_POSTGRES_DSN` to an
isolated loopback database named `metronome_test_ownership` with an administrator
fixture account and local trust authentication, then run
`python -m pytest tests/test_sql_ownership_postgres.py -q -ra`. Never use the
production uploader DSN. The fixture creates unique roles/schema, runs the
application loader as a non-superuser, and removes only those test objects.
CI repeats these transactions on PostgreSQL 14 and 18. PostgreSQL 14 skips the
separate SET membership option case because that option starts in version 16.

For SO-13: save the role in Users; select that person as Flow owner; save;
start a **new** run; wait for SQL commit; open expanded logs and check the
committed owner; refresh the target table's Properties in pgAdmin. Repeat once.
Saving Users alone never alters a database. Clearing/deleting a profile leaves
already transferred ownership in place. Queued runs, saved SQL-only retries,
and previously exported portable files retain their original identity; after
correcting a profile, start a new run or regenerate the portable file.

Work-PC/pgAdmin/ASAP cases remain NOT RUN until actual evidence exists.

## Cleanup and rollback

Delete only designated disposable fixtures and stop their test servers. Keep
test Flow schedules disabled. Reverting application code does not undo a
previously committed PostgreSQL ownership transfer; restoring a real owner is
a separate explicit database action, performed by an authorized administrator.

## Design references

- [PostgreSQL ALTER TABLE ownership requirements](https://www.postgresql.org/docs/current/sql-altertable.html).
- [PostgreSQL inherited role privileges](https://www.postgresql.org/docs/current/functions-info.html).
- [PostgreSQL SET ROLE](https://www.postgresql.org/docs/current/sql-set-role.html).
