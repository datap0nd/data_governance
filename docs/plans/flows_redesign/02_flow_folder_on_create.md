# Plan 2 — Stable per-flow folders

## Goal
New flows without a legacy destination get
<root>/<Source>/<safe name> (id <id>)/Downloads and Scripts.

## Current state
create_flow inserts a path or Local private URI; update_flow preserves that URI.
Jobs and artifacts freeze absolute paths. Direct publishing has a folder-wide
lock independent of SQL target locking (app/routers/pipelines.py:402).

## Design
- Add nullable flow_folder/folder_slug and folder_state default unmanaged.
- Accept explicit destinations for legacy clients while enforcement is off.
  New builder omits target; managed updates preserve server-derived Downloads.
- INSERT, exclusive mkdir, manifest and UPDATE share a DB transaction, but
  filesystem effects need compensation: remove only newly created empty folders
  and owned metadata, never user content. Refuse an existing foreign manifest.
- ID suffix avoids case-insensitive collisions. Sanitize device names (including
  extensions), separators, controls, trailing dots/spaces and excessive length.
- flow_layout owns atomic manifests, schema, ID, adapter and display name.
- Keep physical folders stable on rename; mark deleted without moving/deleting
  data. Preserve existing delete confirmation and pipeline locks.
- POST /{id}/adopt-folder is idempotent, rejects active runs/resource locks,
  returns previous target and never moves old files.
- Local gets visible layout but keeps its private key/source path. Never imply
  snapshots publish into Downloads.
- Include folder fields in output/jobs. Keep shared legacy folder lock tests.

## Step-by-step
Migrations/layout helpers → managed create/edit/delete/adoption → omitted target
validation → readonly destination for managed/new forms → documentation.

## Risks
Server/worker must see the same native root. SQLite rollback does not undo mkdir.
A missing folder or ownership mismatch must not be silently adopted.

## Acceptance criteria
All sources; supplied legacy vs omitted managed target; collisions/I/O rollback;
stable rename; deletion preserving files; idempotent adoption; active-run block;
historic recovery paths remain unchanged.

