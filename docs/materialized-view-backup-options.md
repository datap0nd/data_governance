# Materialized-view definitions: backup and history options

Discussion note, 2026-09-08. This release does not install a backup job, change
PostgreSQL, or introduce a Backups page.

## Recommended approach

Use a **daily schema-only PostgreSQL export for recovery**, alongside
**per-object query history for comparison**. They answer different questions:
what can be recreated, and what definition changed between observations.

PostgreSQL's `pg_dump --schema-only` exports object definitions without table
data. Prefer the complete schema of each selected database over isolated
materialized views, because a selected-object dump does not automatically
include everything that object depends on. Custom-format exports support
selective extraction/restoration with `pg_restore`. Keep owners and privileges
in the archive; cluster roles require separate global coverage. The schema
export is not a backup of the underlying table rows.
[PostgreSQL pg_dump documentation](https://www.postgresql.org/docs/current/app-pgdump.html)

pgAdmin's Backup interface uses PostgreSQL's dump/restore utilities. Its
separately installed **pgAgent** can run those utilities on a daily schedule;
Windows Task Scheduler is another scheduling option. pgAdmin does not need
to stay open when an independent scheduling service owns the job.
[pgAdmin Backup and Restore](https://www.pgadmin.org/docs/pgadmin4/latest/backup_and_restore.html),
[pgAgent](https://www.pgadmin.org/docs/pgadmin4/latest/pgagent.html)

## What Metronome already has

- [PostgreSQL dependency scanning](../app/scanner/pg_deps.py) observes the SELECT
  definition returned by `pg_matviews` for tracked materialized views.
- [Query versioning](../app/query_history.py) stores changed definitions, hashes,
  previous-version links, scan IDs and detection times in `governance.db`.
- [Query-history APIs](../app/routers/query_history.py) support history and
  comparisons in source details. Existing orphan cleanup protects sources with
  recorded versions.
- The [existing daily SQLite backup](../app/scanner/runner.py) backs up
  Metronome's own database. It is not a PostgreSQL schema backup.

That history is useful but insufficient for the requested protection. Its
scope follows active report/flow/dependency sources rather than every
materialized view in every database. It does not include a full recreation
package with indexes, database ownership, grants, comments and storage
properties. It does not record an explicit object-deletion event or expose a
recovery archive browser.

## Daily snapshots versus exact change history

The catalog exposes the current reconstructed definition, not old versions.
[PostgreSQL pg_matviews](https://www.postgresql.org/docs/current/view-pg-matviews.html)

A scheduled snapshot records what was present when it ran. It can miss several
edits, or an object created and dropped, between captures. Metronome's existing
`detected_at` timestamp is an observation time; its assigned owner is not proof
of the PostgreSQL user who executed the change.

For exact DDL events, PostgreSQL supports event triggers. A custom audit setup
can record the actor and command time and maintain definitions as they change.
It must already be installed when the event occurs. The `sql_drop` hook runs
after catalog removal, so recovery needs an earlier preserved definition.
Event-trigger installation requires a PostgreSQL superuser and careful testing.
[Event-trigger behavior](https://www.postgresql.org/docs/current/event-trigger-definition.html),
[CREATE EVENT TRIGGER](https://www.postgresql.org/docs/current/sql-createeventtrigger.html)

## A possible Backups section

If selected for a subsequent implementation, give recovery its own small
section, independent of whether the main Scanner completes:

1. Configure databases, daily time and a local folder or UNC shared folder.
2. Write uniquely timestamped archives and a manifest of scope, objects,
   capture time, tool/server version, checksum and actual result. Publish an
   archive only after the dump succeeds; retain the last successful archive
   when the destination or database is unavailable.
3. Show last successful backup, failures, retained versions and deleted objects.
   Reuse query-history comparisons for readable definition changes.
4. Let an operator download a selected recreation script and inspect it. Test
   restore into an isolated database before claiming recovery works. Recreating
   a materialized view also depends on its sources; repopulation needs a refresh.
5. Apply a retention policy only to verified backup files. Use a separate,
   access-controlled destination to protect against loss of the Metronome host.

The configured service account must be able to reach the chosen destination.
For a Windows service, use a UNC path rather than relying on a user's mapped
drive. A local staging archive can be copied to the shared destination, but a
failed copy must remain visible as a backup failure.

An old view cannot be recovered retroactively from a snapshot taken after its
deletion. Existing backups, Metronome query versions, saved SQL files or an
already-configured audit may still provide evidence; check those before
concluding its definition is lost.
