# Plan 1 — Paths settings page and the flows-root sandbox

## Goal

Add a **System > Paths** page where the owner sets the Metronome flows root
(and any future managed folder), and make that root a hard boundary: every
path a flow configures, reads, or writes must resolve inside
`<flows_root>\<Source>\…`. A path outside the root is rejected at the API
and again by the worker.

Target folder structure (the "current folder structure" from the request):

```
<flows_root>\            e.g. D:\metronome\flows
  ASAP\
  GSCM\
  Outlook\
  Local\
```

## Current state (main @ c527be4)

- No notion of a flows root exists. `flows.target_folder` is a free-text
  absolute path validated only by `_is_absolute_worker_path`
  (`app/routers/flows.py:451-457`: leading `/`, `\\`, or `X:\`). No
  allowlist, normalization, traversal, or existence check
  (`flows.py:797-801`).
- `local_file_path` (From-file flows) and `transform_script_path` get the
  same absolute-only check (`flows.py:701-705`, `:851-859`).
- The worker trusts the job: `_prepare_run_folder` only checks the folder
  exists (`app/flow_worker.py:5915`).
- The only shell integration, `POST /api/scanner/open-path`
  (`app/routers/scanner.py:1494-1519`), opens any path.
- Settings storage is `app_settings` (`app/settings.py`, `get_setting` /
  `set_setting`); no key stores a path today. Filesystem roots come from
  `app/config.py` env vars.
- "System" is a nav-group in `index.html`
  (`data-pages="changelog,eventlog,faq,scanner,ai,updates,premiumviewers"`),
  each entry a route in the `pages` map (`app/static/app.js:12269-12289`).
  The closest template for a settings page is System > Updates
  (`renderUpdates` at `app.js:10334-10446`, `<section class="settings-panel">`
  with `.section-header`).
- Install layout (`setup.ps1:62-70`): `$CodeDir` = repo checkout,
  `$ProjectDir` = its parent, `governance.db` in `$ProjectDir`. Service env
  is injected via NSSM `AppEnvironmentExtra` (`setup.ps1:441-450`).

## Design

### Settings model

New module `app/flow_paths.py` (server and worker both import it; it must
stay free of FastAPI imports).

```python
SETTING_FLOWS_ROOT = "flows_root"
SOURCE_FOLDERS = {                # adapter -> folder name (fixed, not user-editable)
    "asap_portal": "ASAP",
    "gscm_portal": "GSCM",
    "outlook_attachment": "Outlook",
    "local_file": "Local",
    "web_export": "Web",
}
MANAGED_SUBFOLDER = ".metronome"  # server-private state under the root (plan 3/8)

def default_flows_root() -> str          # DG_FLOWS_ROOT env, else Path(DB_PATH).parent / "metronome" / "flows"
def get_flows_root() -> str              # app_settings value, else default; always normalized
def set_flows_root(value: str) -> dict   # validates, creates root + source folders, stores, returns status
def normalize(path) -> str               # ntpath/posixpath normcase+normpath, strips \\?\ prefixes, resolves realpath when it exists
def source_folder_name(adapter: str) -> str
def source_folder(root, adapter) -> str
def is_inside(path, root) -> bool        # commonpath on normalized, realpath-resolved values; False for symlinks/junctions escaping the root
def assert_inside(path, root, *, label) -> str   # raises PathOutsideRoot(ValueError) with a user-facing message
def relative_to_root(path, root) -> str  # for display and for the job payload
def root_status(root) -> dict            # exists, writable, missing source folders, free bytes, is_remote (reuse app/path_safety.is_remote_file_path)
```

Rules encoded in `is_inside`:

- Compare on `os.path.realpath` of the deepest existing ancestor plus the
  remaining tail, so a junction inside the root that points outside is
  rejected. Reuse `flow_publish.normalize_target_path` for the Windows
  `\\?\UNC\` handling instead of duplicating it.
- `..` segments, drive-relative paths (`C:folder`), and paths equal to the
  root itself are rejected (the root is not a valid target; only
  `<Source>\…` is).
- The `.metronome` managed subfolder is never a valid user target.

### Which paths are sandboxed

| Field | Rule after this plan |
|---|---|
| `flows.target_folder` (portal, outlook) | must be inside `<root>\<SourceFolder>\` for the flow's adapter. Plan 2 makes it derived, so after plan 2 this check is a consistency guard. |
| `flows.local_file_path` (From file) | must be inside `<root>\Local\`. **Assumption**: the request's "only read and write inside these folders" includes source files. See Open questions. |
| `flows.transform_script_path` | must be inside the root (plan 3 narrows it to `<flow_folder>\Scripts\`). |
| `POST /api/flows/transform-script` upload destination | moves from `Path(DB_PATH).parent / "flow_scripts"` to `<root>\.metronome\uploads\` in this plan; plan 3 moves it again to the flow's `Scripts\`. |
| `POST /api/scanner/open-path` | unchanged for scanner paths; a new flows-specific endpoint (plan 5) is root-restricted. |

### API

New router `app/routers/system_paths.py`, mounted at `/api/system/paths`
(register in `app/main.py` next to the other routers):

- `GET /api/system/paths` → `{ flows_root: {value, default, source: "setting"|"env"|"default", status: root_status(...)}, source_folders: [{adapter, name, path, exists}], flows_outside_root: [{id, name, target_folder}] }`.
- `PUT /api/system/paths` body `{flows_root: str, create: bool}` →
  validates (absolute, not a file, not inside the code checkout, not the
  drive root), creates the root and the four source folders when
  `create` is true, writes the setting, logs an `event_log` row
  (`log_event` from `app/routers/eventlog.py`, the same way flows log), returns
  the GET payload. Changing the root while any flow run is active returns
  409 with the run ids.
- `POST /api/system/paths/validate` body `{path: str, kind: "target"|"source_file"|"script", adapter?: str}` →
  `{ok, normalized, relative, reason}`. The builder (plan 7) calls this for
  live validation.

Access: same as other settings routes (`has_app_access` is currently
`True` for all). Keep the `require_app_access` hook call for symmetry.

### Validation points in flows

- `FlowWrite.validate_flow` (`flows.py:680`) cannot read settings (pydantic
  validator, no DB). Keep the cheap absolute-path check there and add the
  root check in `_validate_flow_selections` (`flows.py:1332`), which already
  has `db` and runs for create and update. Add a helper
  `_assert_flow_paths_inside_root(db, body)` called from both
  `create_flow` (`:2566`) and `update_flow` (`:2648`) before the INSERT/UPDATE.
- Error messages name the folder: `"Target folder must be inside D:\metronome\flows\ASAP. Got E:\exports."`
- `_build_job` (`flows.py:1464`) adds a `paths` block to the job:
  `{"flows_root": root, "source_folder": ..., "layout_version": 1}`. The
  worker re-checks `downloads.target_folder`, `local_file.path`, and
  `transformation.script_path` against `paths.flows_root` in
  `execute_job` before any I/O, and fails with stage `path_outside_root`.
  This is the defense-in-depth layer for jobs queued before a root change.

### Existing flows that are outside the root

Never move files. On startup and on every `GET /api/system/paths`, compute
`flows_outside_root`. For those flows:

- `_flow_out` gains `path_status: "ok" | "outside_root" | "root_missing"`.
- `queue_flow_run_service` / `queue_flow_run` refuse to queue a flow whose
  `path_status != "ok"` with a 409 that names the folder; the scheduler
  (`queue_due_flows`) skips it and records `last_error`.
- The Flows list (plan 5) shows an amber "Outside flows root" badge with a
  link to the builder; the builder (plan 7) offers "Move into flows root"
  which only rewrites the configured path (plan 2 creates the new folder).
  Copying historical downloads is left to the owner and documented.

### System > Paths page

- New route `paths` added to the System nav-group in `index.html`
  (`data-pages="...,paths"`) and to the `pages` map in `app.js`.
- Renderer `renderPaths()` between markers
  `// ── System > Paths ──` and the next section, with sub-functions
  `_pathsPanelHtml(state)` and `_pathsFlowsOutsideHtml(rows)` so a
  `tests/test_paths_display.mjs` can slice them.
- Panels (all `<section class="settings-panel">`):
  1. **Flows root** — current value, source (setting / env / default),
     status line (exists, writable, remote share warning, free space),
     input + **Save** + **Create missing folders**.
  2. **Source folders** — read-only table `ASAP / GSCM / Outlook / Local`
     with the resolved path and an exists check.
  3. **Flows outside the root** — table of flows with the configured
     path and an **Edit flow** link; empty state "All flows are inside the
     flows root."
- Copy: state the consequence plainly, e.g. "Changing the root does not
  move existing files. Flows that point outside the new root stop being
  scheduled until they are edited."

### setup.ps1

- Compute `$FlowsRoot = "$ProjectDir\metronome\flows"` unless
  `DG_FLOWS_ROOT` is already set; create it and the four source folders;
  add `DG_FLOWS_ROOT` to the NSSM `AppEnvironmentExtra` for both
  `MXAnalytics` and `MXFlowsWorker` and to the headed task's environment
  (the headed task inherits the interactive user env, so also persist it
  with `[Environment]::SetEnvironmentVariable(..., "User")`).
- Add the root to the "paths" summary printed at the end of setup.

## Step-by-step

1. `app/flow_paths.py` with the functions above and unit tests
   `tests/test_flow_paths.py` (pure functions, `tmp_path` based; include a
   junction/symlink escape case guarded by `os.symlink` availability, a
   `\\?\UNC\` case using `flow_publish.normalize_target_path`, and the
   drive-root and code-checkout rejections).
2. `app/database.py`: no schema change; the setting key is new. Add a
   startup hook in `app/main.py` lifespan that calls
   `flow_paths.get_flows_root()` and logs a warning event when the root
   does not exist.
3. `app/routers/system_paths.py` + registration + `tests/test_system_paths.py`
   (direct router calls with `SimpleNamespace(state=SimpleNamespace(actor=...))`
   like `tests/test_flows.py`; cover GET default, PUT create, PUT 409 while
   a run is active, validate endpoint per kind).
4. `app/routers/flows.py`: `_assert_flow_paths_inside_root`, `path_status`
   in `_flow_out`, queue refusal, `paths` block in `_build_job`, upload
   destination change. Tests in `tests/test_flows.py`:
   `test_target_folder_outside_flows_root_is_rejected`,
   `test_target_folder_must_match_source_folder`,
   `test_flow_outside_root_is_not_queued_and_marked`,
   `test_job_carries_flows_root`. Update the `_flow()` helper baseline
   (`tests/test_flows.py:~940`) to use a `tmp_path`-based root set through
   `flow_paths.set_flows_root`, and the equivalent baselines in
   `test_flow_outlook.py:30`, `test_flow_target_roundtrip.py:85`,
   `test_flow_local_file.py`.
5. `app/flow_worker.py`: `_assert_job_paths(job)` at the top of
   `execute_job` and in `execute_local_file_job`; test in
   `tests/test_flow_worker_discovery.py` using a synthetic job with a path
   outside `paths.flows_root` asserting the failure stage.
6. Frontend: nav entry, route, `renderPaths`, `bindPathsPage`, CSS reuse of
   `.settings-panel`; `tests/test_paths_display.mjs` + workflow step.
7. `setup.ps1` changes + `tests/test_unattended_update_scripts.py` style
   assertion that setup creates the root and injects `DG_FLOWS_ROOT`
   (mirror `test_setup_installs_headless_flow_worker_service`).
8. Docs: new `docs/flow_paths.md` (rules, migration guidance), README
   "Configurable report-download flows" paragraph, and update
   `docs/local_file_flows.md` which currently says "There is no Metronome
   path allowlist".

## Migration and rollout

- First start after upgrade: root defaults to `<ProjectDir>\metronome\flows`
  and is created. Every existing flow is outside it → all show
  `outside_root`, scheduled runs are skipped with a clear `last_error`.
  This is intentional but disruptive; the release note must say: "Set
  System > Paths first, then edit each flow (or run the adopt action) so
  scheduled downloads resume."
- Provide `POST /api/system/paths/adopt` (plan 2 wires the folder
  creation): for each flow whose current `target_folder` is already inside
  the root and under the right source folder, mark it `ok` without
  changes.

## Risks and open questions

- **Local source files** (assumption above). If the owner wants From-file
  flows to keep reading UNC shares, replace the `Local\` rule with a
  read-only exception list stored under a second Paths setting
  (`external_read_roots`), still rejecting writes outside the root.
- **Network root.** If the root is on a share, every unchanged-file check
  and retention op crosses the network. `root_status.is_remote` surfaces a
  warning; no other behavior change.
- **Direct-file private store.** Its key is the normalized target path.
  Plans 2 and 3 handle re-keying; this plan does not touch it.

## Acceptance criteria

- A flow pointing at any path outside `<root>\<Source>\` cannot be created,
  updated, queued, or executed; the error names both paths.
- System > Paths shows and edits the root, creates the source folders, and
  lists outside-root flows.
- `DG_FLOWS_ROOT` bootstraps the default on a fresh install; the setting
  overrides it.
- Full pytest suite green on Ubuntu and Windows CI; `node --check` green;
  new `.mjs` test in the workflow.
