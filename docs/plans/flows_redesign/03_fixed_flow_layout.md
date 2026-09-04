# Plan 3 — Fixed per-flow layout: `Downloads\` and `Scripts\`

## Goal

Every flow folder has the same inner structure, owned by one module, so
the worker, the UI, and the standalone script all agree on where things
are:

```
<flows_root>\ASAP\Weekly sell-out\
  flow.json                 # manifest (layout_version, ids, names, config hash)
  Downloads\                # run folders (#<run>_<dd-mm-yyyy>) or direct files
  Scripts\
    run_weekly_sell_out.py  # standalone launcher (plan 4)
    flow_job.json           # frozen job the launcher runs (plan 4)
    transform.py            # optional transformation script (moved here)
```

Only `Downloads` and `Scripts` for now; the layout is versioned so more
subfolders can be added later without guessing.

## Current state (main @ c527be4)

- Run folders are created by `flow_retention.create_run_folder(storage_root, run_id, flow_id)`
  (`app/flow_retention.py:42-90`) directly under the target. Retention
  deletes only direct children of the *registered* root that match
  `RUN_FOLDER_RE` and carry `.metronome_run.json`
  (`_gate_reason`, `:111`).
- Direct-file mode stores runs privately at
  `<profile_dir>\run_artifacts\<sha256(target)[:24]>` and publishes
  deliverables into the target (`app/flow_publish.py:47`, `:363`).
- Transform scripts are uploaded to `Path(DB_PATH).parent / "flow_scripts"`
  (`app/routers/flows.py:2628`) and referenced by absolute path in
  `flows.transform_script_path`; their output goes to
  `<run folder>\script_results` derived from the first artifact's path
  (`app/flow_worker.py:5049`).
- `setup.ps1:378-382` also copies the repo `transforms\` folder to
  `\\MX-SHARE\…\Metronome\flow_scripts`.

## Design

### `app/flow_layout.py`

```python
LAYOUT_VERSION = 1
MANIFEST_NAME = "flow.json"
DOWNLOADS = "Downloads"
SCRIPTS = "Scripts"
SUBFOLDERS = (DOWNLOADS, SCRIPTS)

def create_flow_folder(source_dir, slug, *, flow_id, name, adapter) -> str
def ensure_layout(flow_folder) -> dict        # creates missing subfolders; returns {created: [...], missing_before: [...]}
def layout_status(flow_folder) -> dict        # {exists, downloads, scripts, manifest, manifest_matches_flow_id, layout_version}
def downloads_dir(flow_folder) -> str
def scripts_dir(flow_folder) -> str
def manifest_path(flow_folder) -> str
def read_manifest(flow_folder) -> dict | None
def write_manifest(flow_folder, data: dict) -> None   # atomic write via temp + os.replace
```

Manifest contents (owner: this module; plan 4 adds `script` keys):

```json
{
  "schema": "metronome-flow-folder",
  "layout_version": 1,
  "flow_id": 12,
  "flow_name": "Weekly sell-out",
  "source_adapter": "asap_portal",
  "source_folder": "ASAP",
  "created_at": "2026-09-04T10:00:00Z",
  "updated_at": "2026-09-04T10:00:00Z",
  "config_sha256": "…",
  "deleted_at": null
}
```

### Where each thing lives after this plan

| Item | Location | Notes |
|---|---|---|
| Run folders (`run_folders` mode) | `Downloads\#<run>_<dd-mm-yyyy>\` | `register_folder` is called with the `Downloads` path so `_gate_reason` keeps working. |
| Direct files (`direct_replace`) | published into `Downloads\`; private run folders at `<flows_root>\.metronome\artifacts\<store>\<hash>\#<run>_…` | Store moves off the worker profile so every worker on the machine shares it (plan 8 needs this). |
| Private snapshots (file flows) | `<flows_root>\.metronome\artifacts\<store>\<local-file hash>\…` | Same move. |
| Transformation script | `Scripts\<original name>` | Upload endpoint takes `flow_id`; before the flow exists (create-time) the file is parked in `<flows_root>\.metronome\uploads\<uuid>\` and moved into `Scripts\` on create. |
| `script_results` | `<run folder>\script_results` (unchanged) | Output stays with the run for retention. |
| Standalone script + frozen job | `Scripts\` | Plan 4. |

### Artifact store move

`flow_publish.private_target_root(profile_dir, target)` and
`private_local_file_root` gain a `store_root` parameter. The worker passes
`store_root = <flows_root>\.metronome\artifacts` from the job's `paths`
block (plan 1); `artifact_store_id` (`flow_publish.py:40`) becomes
`sha256(hostname|normcase(store_root))[:24]` so it is shared across
profiles on the same machine but still differs per machine (remote runners
keep their own store).

Migration for existing Direct-file / file-source flows' recovery data
(Resume, SQL Retry pins):

- On first start of a worker with the new code, if
  `<profile_dir>\run_artifacts` exists and the new store root does not
  contain the same `<hash>` folders, **move** each hash folder (same
  volume: `os.replace`; else copy then delete after checksum) and write
  `migrated_from` into `.metronome_target.json`. The worker registers with
  both the new `artifact_store_id` and `previous_artifact_store_ids`;
  `claim_run` (`flows.py:4452-4493`) accepts a queued run whose
  `required_artifact_store_id` is in either.
- This is the only automatic file move in the whole plan set. It touches
  Metronome-private folders only (marker-checked), never user files.

### Self-healing

- Worker: `_prepare_run_folder` calls `flow_layout.ensure_layout(job["flow"]["folder"])`
  before creating the run folder; a missing `Downloads` is recreated
  (it is Metronome-owned); a missing *flow folder* fails the run with
  stage `flow_folder_missing` and the server sets
  `folder_state='missing'`.
- Server: `GET /api/flows/{id}` and the list include
  `layout: layout_status(...)`; the list shows a badge when anything is
  missing (plan 5). `POST /api/flows/{id}/repair-layout` re-creates
  subfolders and the manifest.

### Transform-script upload changes

- `POST /api/flows/transform-script` → accept optional `flow_id` form
  field. With `flow_id`: save to `Scripts\<name>` (collision suffix as
  today), set `transform_script_path`, return it. Without: park under
  `.metronome\uploads\<uuid>\<name>` and return a `pending_upload_id`;
  `create_flow` moves it into `Scripts\` and rewrites the path.
- Validation: `transform_script_path` must be inside `Scripts\` of that
  flow (`flow_paths.assert_inside(path, scripts_dir)`).
- Drop the `Path(DB_PATH).parent / "flow_scripts"` location; keep reading
  legacy absolute paths inside the root until the flow is edited, then
  copy the script into `Scripts\` on save (never move — the old shared
  copy may be used by other flows).
- `setup.ps1` bundled `transforms\` publishing stays, but the builder's
  "Browse…" no longer offers the share; users pick a local file. Document.

## Step-by-step

1. `app/flow_layout.py` + `tests/test_flow_layout.py` (create, ensure,
   status, manifest atomicity, reserved paths).
2. Retention: no code change in `flow_retention.py` beyond a docstring;
   add `tests/test_flow_retention.py::test_gate_accepts_downloads_root_children`
   proving ops under `Downloads` pass and a run folder placed at the flow
   folder level is rejected.
3. `flow_publish`: `store_root` parameter, `artifact_store_id(store_root)`,
   `previous_artifact_store_ids`, migration helper
   `migrate_profile_store(profile_dir, store_root)`; tests in
   `tests/test_flow_publish.py` (move same volume, copy+verify cross
   volume simulated with monkeypatched `os.replace` raising `OSError`).
4. `flow_worker`: use `paths.artifact_store_root`, call `ensure_layout`,
   register with previous store ids; `tests/test_flow_worker_discovery.py`
   assertions on registration payload and on `register_folder` being
   called with the `Downloads` path (extend
   `test_execute_job_registers_the_folder_before_reporting_progress`).
5. `flows.py`: `claim_run` accepts previous store ids; `layout` in
   `_flow_out`; `repair-layout` endpoint; transform upload changes; script
   path validation. Tests: `test_transform_upload_lands_in_flow_scripts`,
   `test_pending_upload_is_moved_on_create`,
   `test_claim_accepts_previous_artifact_store_id`,
   `test_repair_layout_recreates_subfolders`.
6. `_build_job`: `paths.artifact_store_root`, `flow.folder`,
   `transformation.script_path` (now inside `Scripts`).
7. Docs: `docs/flow_paths.md` "Layout" section with the tree above;
   update `docs/flow_transformation_contract.md` (script location) and
   README (Direct files paragraph now names the shared store path).

## Risks

- **Store move on a busy machine.** Do the migration only when the worker
  holds no run (it runs before `claim`), and skip (log a warning) if the
  old store is locked by another process.
- **Disk budget** moves from the user profile drive to the root's drive.
  Note it in the release note ("Budget three unpinned runs per flow under
  `<root>\.metronome`").

## Acceptance criteria

- New flows have `Downloads`, `Scripts`, `flow.json`; runs land under
  `Downloads`; retention still keeps three.
- Direct-file and file-source flows can Resume and Retry SQL across the
  store move (tested with a pre-seeded profile store).
- Transform scripts uploaded from the builder end up in the flow's
  `Scripts` folder and the worker executes them from there.
