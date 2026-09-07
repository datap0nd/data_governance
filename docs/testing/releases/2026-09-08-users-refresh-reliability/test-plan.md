# Users, TMDL Checker retirement and refresh recovery: test plan

- Code baseline: `3ccbf8487b96a73b68d538808b401cc6510a63e8`.
- Scope: dedicated Users section, optional SQL identities, retired TMDL Checker,
  and reliable refresh-only recovery after committed Flow SQL insertion.
- The existing Dashboard is unchanged. Dashboard experiments were removed.
- Related report: [test-report.md](test-report.md).
- Backup/version-history options are documentation for discussion only. No new
  backup schedule or PostgreSQL event trigger is included.

## Prerequisites

Update the app and workers to the merged release before live testing. Record
their revisions and the browser version. Regenerate portable recorded-Flow
scripts so they include the updated runtime. Preserve existing configuration.
Use clearly named temporary BI/Business profiles and a disposable PostgreSQL
target for live insertion checks; do not use business tables for failure tests.

For view-recovery tests, prepare two materialized views with a known dependency
order and a small input file with a known row count. Configure a failure that
affects only the second view, such as a controlled test-only permission or lock
condition. Keep its setup and cleanup under the database operator's control.

## Test cases

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| U-01 | Open Users. Add a BI profile and a Business profile with name/email; leave one SQL username blank. Search by name and email; filter both roles. | Profiles appear under Users; role filters and combined search work. Optional SQL username is shown as Not linked. |
| U-02 | Edit a profile's name and SQL username. Assign the profile to a test Flow, then change its SQL username again. Reload both pages. | Profile fields persist. Flow keeps the same owner ID and resolves the current name/SQL username. No grants or PostgreSQL connections occur from profile edits. |
| U-03 | Submit an empty name, invalid email in the browser, and a SQL username over 63 UTF-8 bytes. Inject a failed save request in the isolated browser harness and retry. | Invalid entries cannot save; failed requests retain entered work, show a local error and allow retry. Pending requests cannot submit twice or replace the active editor. |
| U-04 | Clear the optional SQL username. Open Delete, choose Keep user, then reopen and delete a disposable profile owning a disposable Flow. Test a failed delete and retry in the harness. | Clearing persists; Keep user restores keyboard focus. Confirmation names the user and explains unassignment. A successful delete keeps the Flow and clears its owner. Failed delete does not remove the row. |
| U-05 | Walk Users with the keyboard; check desktop and 390px widths, search with no matches, an empty directory and a failed initial load/retry. Open Create Artifacts. | Labels/focus/feedback remain clear; tables scroll within the page; Create Artifacts retains asset creation and has no People tab. Owner help points to Users. |
| T-01 | Open navigation and FAQ; call `/api/best-practices`. Run standalone governance and a full scan against fixtures. | TMDL Checker has no page/menu entry; old API returns 410. Neither scan path executes the checker. Schedule/documentation checks still execute. |
| T-02 | Run PBIX/TMDL parsing, PostgreSQL dependency and query-history regressions. Seed historical Checker findings/actions. | Metadata, lineage and query history still work. History is retained; Checker findings cannot generate new owner-summary notifications. |
| D-01 | Compare `app/routers/dashboard.py`, Dashboard render/bind code and shared stylesheet against the baseline. Open the existing Dashboard after update. | No dashboard redesign or new dashboard endpoint is included. Existing dashboard behavior and visuals remain available; only the requested navigation changes appear. |
| R-01 | Run file and recorded flows in append and replace modes, using a known input and two dependent materialized views. | SQL commit completes before any refresh. Views refresh in dependency order. Capture run IDs and protected count comparisons. |
| R-02 | Simulate COPY and commit failures with synthetic connections. | No materialized-view refresh starts; the run cannot claim success. Unknown SQL outcomes remain subject to reconciliation. |
| R-03 | After a successful insertion, fail creation of the refresh connection or fail refresh of the second view. | The run records SQL committed and refresh failed; completed-view checkpoints survive; remaining views are skipped. |
| R-04 | On a portable recorded Flow with a failed second view, run normally, then use `--retry-views` while failure persists, then remove the failure and retry again. | Normal rerun is blocked. Retry opens no browser and does not reinsert SQL rows. Failed retry retains recovery files. Successful retry skips the completed first view, refreshes the second and clears recovery files. Record target row counts before/after. |
| R-05 | Exercise malformed/non-object/unknown-outcome, missing and different-target SQL journals. Remove, replace or reorder the script's saved view plan; corrupt or mismatch the checkpoint. | Retry refuses execution and preserves recovery files. Recovery requires the original ordered view identities and a matching committed SQL journal; older journals without a plan require reconciliation. |

## Automated commands

Use the dependencies in `.github/workflows/tests.yml`. On Windows, a fresh short
pytest temporary path avoids the existing long-path limitation in generated
Flow handover archives. Check that the chosen directory is disposable before
passing it to pytest, which owns and clears its `--basetemp` directory.

```powershell
python -m pytest tests -q --basetemp C:\MetronomeTestTemp\users-release
node --check app/static/app.js
node --check app/static/users.js
Get-ChildItem tests/test_*.mjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw $_.Name } }
```

The full suite includes Users browser tests with fictional HTTP fixtures and
SQL/refresh tests with synthetic database connections. Neither proves live
portal access, PostgreSQL grants/locks or server deployment.

## Acceptance, cleanup and rollback

- Automated checks must pass on the final source revision, and final PR checks
  must pass. Record live cases separately as PASS, FAIL, NOT RUN or BLOCKED.
- Delete only disposable test profiles/flows; restore test-only SQL permissions
  and locks. Preserve recovery evidence until row counts confirm no replay.
- Roll back code using the normal version workflow if necessary. The additive
  nullable SQL username column may remain; older code ignores it. Do not purge
  people, existing flows, historical Checker findings or query versions.
- A schema-only PostgreSQL backup is a separate proposed feature. No backup or
  restoration claim is established by this release's SQLite/browser fixtures.
