# Plan 2 — Auto-create a flow's folder on creation

## Goal

When a flow is created, Metronome creates
`<flows_root>\<Source>\<FlowName>\` automatically, with the fixed inner
layout from plan 3, and stores that folder as server-owned state. Users no
longer type a target folder anywhere.

## Current state (main @ c527be4)

- `create_flow` (`app/routers/flows.py:2566-2614`) inserts the user-supplied
  `target_folder` and `filename_template`. For From-file flows
  `_resolve_flow_source` (`:1225`) overwrites `target_folder` with an opaque
  `metronome-private://local-file/<uuid4>` key
  (`flow_publish.new_local_file_storage_key`, `app/flow_publish.py:84`).
- `update_flow` (`:2648-2745`) re-pins the private key for file flows and
  otherwise accepts a new `target_folder`.
- The worker creates only run folders inside the configured target
  (`flow_retention.create_run_folder`, `app/flow_retention.py:42-90`) and
  fails if the target does not exist (`app/flow_worker.py:5915`).
- Flow names are `UNIQUE` in SQLite but never sanitized for the
  filesystem. `_render_filename` (`flow_worker.py:262`) sanitizes rendered
  filenames only; `_slug_key` (`flow_worker.py:2601`) exists but serves
  portal matching.
- Direct-file mode keys its private store by
  `sha256(normalize_target_path(target))[:24]` under the worker profile
  (`flow_publish.private_target_root`, `:47`), guarded by
  `.metronome_target.json`.
- Folder locks: `flow_publish_resource_key_from_job` /
  `flow_target_resource_key_from_job` (`app/routers/pipelines.py:385-410`)
  derive a lock key from `(target_folder, filename_template)`.

## Design

### Schema

Migration in `app/database.py` `MIGRATIONS`:

```sql
ALTER TABLE flows ADD COLUMN flow_folder TEXT;          -- absolute, server-owned
ALTER TABLE flows ADD COLUMN folder_slug TEXT;          -- last path segment, stable across display-name edits unless renamed
ALTER TABLE flows ADD COLUMN folder_state TEXT NOT NULL DEFAULT 'unmanaged';
  -- 'managed' | 'unmanaged' (pre-existing flow, folder not created by Metronome) | 'missing' (managed folder vanished)
CREATE UNIQUE INDEX IF NOT EXISTS idx_flows_folder ON flows(flow_folder);
```

`target_folder` stays `NOT NULL` for compatibility but becomes derived:
`target_folder = <flow_folder>\Downloads` for portal and outlook flows.
File flows keep the private key in `target_folder` (unchanged) and use
`flow_folder` for the visible layout (their Downloads folder holds the
source file per plan 1's Local rule).

### Slug rules (`flow_paths.flow_folder_slug(name)`)

- Trim, collapse whitespace to single spaces, strip characters in
  `SAFE_NAME_RE`'s reject set (`<>:"/\|?*` and controls), strip trailing
  dots and spaces (Windows), cap at 80 chars.
- Reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`)
  get a `_flow` suffix.
- Case-insensitive collision with an existing sibling folder or another
  flow's `folder_slug` under the same source → append ` (id <flow_id>)`.
  The id is known only after INSERT, so creation is: INSERT the row with
  `flow_folder = NULL` inside the transaction, compute the slug with the
  new id, `mkdir`, then UPDATE the same row before COMMIT. If `mkdir`
  fails the transaction rolls back and the API returns 500 with the OS
  error; no half-created flow remains.

### Creation flow (`create_flow`)

1. `_resolve_flow_source`, validators, owner and SQL checks as today.
2. `root = flow_paths.get_flows_root()`; `source_dir = flow_paths.source_folder(root, adapter)`;
   ensure `source_dir` exists (create it; it is inside the root by
   construction).
3. INSERT row (target_folder set to a placeholder equal to `source_dir`
   so `NOT NULL` holds), then `flow_layout.create_flow_folder(source_dir, slug, flow_id, name, adapter)`
   (plan 3 owns `flow_layout`; in this plan it creates the folder plus
   `Downloads\` and `Scripts\` and writes `flow.json`).
4. UPDATE `flow_folder`, `folder_slug`, `folder_state='managed'`,
   `target_folder = flow_folder\Downloads` (except file flows).
5. `log_event(... "flow_folder_created" ...)` and return `_flow_out`, which
   now includes `flow_folder`, `folder_state`, and `folder_relative`
   (`ASAP\Weekly sell-out`) for display.

`FlowWrite.target_folder` becomes optional and **ignored** for non-file
flows (log a deprecation warning event when a client still sends it, so
old browser tabs keep working). Plan 7 removes the input.

### Rename (`update_flow`)

- If `name` changed and `folder_state == 'managed'`:
  - If a run is active (queued/claimed/running) → 409 "Rename after the
    current run finishes."
  - Compute the new slug. If it differs, `os.rename(flow_folder, new)`
    inside a try; on `PermissionError` (Explorer or Excel holding a
    handle) → 409 "Close files in <folder> and retry." Nothing partial:
    the DB update happens only after the rename succeeds.
  - Update `flow_folder`, `folder_slug`, `target_folder`, and the
    `flow.json` manifest.
- Direct-file flows: the private store is keyed by the target path. On
  rename, re-key by renaming the store folder too
  (`flow_publish.rekey_private_target(profile_dir, old_target, new_target)`)
  — but the server cannot reach the worker's profile. So instead record
  `previous_target_folders_json` on the flow (append old path), and let
  `_validated_resume_artifacts` (`flow_worker.py:5749`) accept artifacts
  whose store key matches any previous target. Plan 3's move of the store
  under the root removes this problem for good; until then this is the
  compatibility path.

### Delete (`delete_flow`, `:2793`)

- Never delete the folder. Write `<flow_folder>\flow.json` with
  `deleted_at` and rename the folder to `<slug> (deleted <yyyy-mm-dd>)`
  only if no run is active and the rename succeeds; otherwise leave it and
  record the event. The dialog copy (`_flowDeleteDialog`,
  `app.js:11519`) states that files stay on disk and names the folder.

### Existing flows (migration)

- Migration sets `folder_state='unmanaged'` for every existing row and
  leaves `flow_folder NULL`.
- `POST /api/flows/{id}/adopt-folder` (called by the builder's "Move into
  flows root" action, plan 7, and by System > Paths "Adopt all", plan 1):
  creates the managed folder from the current name, sets
  `target_folder` to the new `Downloads`, and leaves old files where they
  are. Response includes `previous_target_folder` so the UI can show it.
- Runs are refused for `unmanaged` flows only when the old
  `target_folder` is also outside the root (plan 1's rule); an unmanaged
  flow already inside the root keeps working until adopted.

### Locks

`flow_publish_resource_key_from_job` / `flow_target_resource_key_from_job`
keep using `target_folder`, so per-flow folders naturally give each flow
its own publish lock. Two flows can no longer share a target folder,
which removes the cross-flow folder contention case entirely
(`test_direct_flows_share_a_folder_lock_even_with_different_sql_targets`
must be rewritten to assert the lock is per flow folder).

## Step-by-step

1. `app/database.py` migration + `flow_paths.flow_folder_slug` + tests
   (`tests/test_flow_paths.py`: slug cases, reserved names, collision suffix).
2. `app/flow_layout.py` (plan 3) minimal version: `create_flow_folder`,
   `manifest_path`, `write_manifest`, `read_manifest`.
3. `create_flow` / `update_flow` / `delete_flow` changes; `_flow_out`
   fields; `adopt-folder` endpoint.
4. `_build_job`: `downloads.target_folder` continues to carry the derived
   Downloads path; add `flow.folder` and `flow.folder_relative` for the
   worker log and the standalone script (plan 4).
5. Tests (`tests/test_flows.py`):
   - `test_create_flow_creates_managed_folder_and_derives_target`
   - `test_create_flow_rolls_back_when_folder_creation_fails` (monkeypatch
     `os.makedirs` to raise)
   - `test_slug_collision_appends_flow_id`
   - `test_rename_renames_folder_when_idle_and_refuses_while_running`
   - `test_delete_keeps_files_and_marks_manifest`
   - `test_adopt_folder_moves_configuration_not_files`
   - `test_target_folder_in_body_is_ignored_for_managed_flows`
   - rewrite of the shared-folder lock test.
   Outlook/file equivalents in `tests/test_flow_outlook.py` and
   `tests/test_flow_local_file.py` (file flows still keep the private key).
6. Frontend: none beyond showing `folder_relative` in the list (plan 5)
   and removing the input (plan 7). Until plan 7 lands, hide the
   `#flow-target-folder` input when creating (`_flowBuilderHtml`,
   `app.js:11232`; `_flowOutlookBuilderHtml`, `:11080`) and show the
   read-only computed path instead.
7. Docs: README flows section ("Metronome creates one folder per flow
   under the flows root…"), `docs/flow_paths.md` section "Folder per flow".

## Risks

- **Long paths.** `<root>\Outlook\<80-char slug>\Downloads\#123_04-09-2026\<filename>`
  can pass 260 chars when the root is deep. Enable long-path aware
  handling: the worker already uses `\\?\` normalization in
  `flow_publish`; add a preflight in `set_flows_root` warning when the
  root is longer than 120 chars.
- **Renames while Explorer is open** are refused, not forced. Users get
  a clear message.
- **Two Metronome instances sharing a root** are not supported; the
  unique index is per database only.

## Acceptance criteria

- Creating a flow of each source type creates
  `<root>\<Source>\<slug>\Downloads` and `\Scripts` and returns
  `flow_folder`. Runs write into `Downloads`.
- Renaming a flow renames the folder when idle; delete never removes
  files.
- Existing flows continue to run when their folder is inside the root
  and are adoptable in one call otherwise.
