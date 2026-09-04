# Codex review prompts — one chat per plan

Paste each block into its own Codex chat with the repository
`datap0nd/data_governance` (branch `main`) attached. Every prompt asks
the model to revise the plan against the real code, not to implement it.
The shared preamble is repeated in each block so chats stay independent.

---

## Shared preamble (already included in every block below)

> You are reviewing an implementation plan for Metronome, the FastAPI +
> vanilla-JS app in this repository (`app/`, `app/static/app.js`,
> `tests/`). Read the plan file named below from `docs/plans/flows_redesign/`
> on `main`, then verify every claim it makes against the current code:
> file paths, line numbers, function names, table columns, test names,
> and setup.ps1 behaviour. Produce a revised version of the same plan
> file that (1) corrects anything that is wrong or has moved, (2) adds
> steps the plan is missing to be implementable end to end, (3) removes
> or flags steps that would break an existing test or documented
> invariant (README, docs/*.md, SECURITY-style rules), and (4) keeps the
> plan's decisions unless the code makes them impossible, in which case
> explain the conflict and propose the smallest alternative. Keep the
> original section structure and file references style (`path:line`).
> Do not implement anything. Output the full revised markdown.

---

## Chat 1 — Plan 1: Paths settings and sandbox

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/01_paths_settings_and_sandbox.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code: file paths, line numbers, function names, table columns, test names, and setup.ps1 behaviour. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps needed to be implementable end to end, removes or flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible (explain the conflict and propose the smallest alternative). Pay particular attention to: how `FlowWrite` validation (`app/routers/flows.py`) can reach settings without a DB handle; whether `app/path_safety.py` and `app/flow_publish.normalize_target_path` cover UNC and `\\?\` cases; the exact places `target_folder`, `local_file_path`, and `transform_script_path` are validated; how `setup.ps1` injects environment into the NSSM services and the headed task; and which existing tests seed `target_folder` values that the new root rule would reject. Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 2 — Plan 2: Flow folder on create

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/02_flow_folder_on_create.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: the transaction shape of `create_flow` and whether an INSERT-then-UPDATE inside one connection is compatible with `get_db()` and `BEGIN IMMEDIATE` usage; how file-source flows use the `metronome-private://local-file/` key and whether giving them a visible folder conflicts with `flow_publish.private_local_file_root`; the Direct-file private store being keyed by the normalized target path and what a rename does to Resume / SQL Retry (`_validated_resume_artifacts`, `claim_run` store-id checks); the lock keys in `app/routers/pipelines.py` derived from `(target_folder, filename_template)`; and `delete_flow` behaviour. Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 3 — Plan 3: Fixed flow layout

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/03_fixed_flow_layout.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: `app/flow_retention.py` gating (`_gate_reason`, `RUN_FOLDER_RE`, marker file) and whether registering the `Downloads` folder as the storage root is sufficient; `app/flow_publish.py` `private_target_root`, `artifact_store_id`, and `.metronome_target.json` and what a store move implies for `register_worker` / `claim_run` `required_artifact_store_id`; how `_run_transformations` derives `script_results`; the transform upload endpoint and its `flow_scripts` folder; and `setup.ps1` publishing of `transforms/`. Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 4 — Plan 4: Standalone flow script

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/04_standalone_flow_script.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: the exact signatures of `execute_job`, `execute_outlook_job`, `execute_local_file_job`, and `run_worker` in `app/flow_worker.py` and every place they touch the server (`_api`, `report_progress`, `register_folder`, heartbeat); whether `_build_job` in `app/routers/flows.py` can be moved to a router-free module without circular imports; the worker profile lock (`_exclusive_worker_lock`) and Playwright persistent-context constraints; how `app/flow_sql.py` and `app/config.py` obtain `DG_UPLOAD_PG*` and whether reading NSSM `AppEnvironmentExtra` from the registry is realistic on this appliance; `flow_credentials` DPAPI scope; and that the frozen job contains no secrets. Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 5 — Plan 5: Flows list redesign

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/05_flows_list_redesign.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: `_flowListHtml` and `_bindFlowWorkspace` in `app/static/app.js`, the `.mjs` slicing markers used by `tests/test_flows_display.mjs`, the fields `_flow_out` actually returns (is `category_path` available? how is the ASAP download type exposed?), `alertAssetLogo` icons, the `task-modal` / `_flowBindDialog` pattern for the row menu, `app/local_access.is_server_machine`, the existing `POST /api/scanner/open-path`, and the session-0 problem for launching Explorer from a Windows service (compare with how `app/flow_outlook.py` and `app/flow_local_runner.py` use scheduled tasks). Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 6 — Plan 6: Sortable columns

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/06_sortable_columns.md`, `docs/plans/flows_redesign/05_flows_list_redesign.md`, and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of plan 6 that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: the existing `dataTable` / `_filterAndSortDT` / `_renderDT` / `bindDataTables` engine in `app/static/app.js` and whether it can be reused for a grouped table instead of a new sorter; the `sortable` header CSS in `style.css`; how `sessionStorage` state is handled elsewhere; keyboard accessibility conventions in `DESIGN.md` and `PRODUCT.md`; and how the 5-second Flows poll re-renders the list. Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 7 — Plan 7: Builder redesign

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/07_builder_redesign.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: `_flowBuilderHtml`, `_flowOutlookBuilderHtml`, `_flowCollectBuilder`, `syncAsapDownloadControls`, `syncLocalFileWorksheet`, the report-scan polling, the replicate-settings feature, the transform Browse upload, and every element id those handlers depend on; the `DESIGN.md` builder contract; which `FlowWrite` fields are required per `source_type` so the step "complete" rules match server validation; and what happens to `target_folder` in the payload before plan 2 lands. Keep the original section structure. Do not implement anything. Output the full revised markdown.

## Chat 8 — Plan 8: Parallel runners

You are reviewing an implementation plan for Metronome, the FastAPI + vanilla-JS app in this repository (`app/`, `app/static/app.js`, `tests/`). Read `docs/plans/flows_redesign/08_parallel_runners.md` and `docs/plans/flows_redesign/00_README.md` on `main`, then verify every claim against the current code. Produce a revised version of the plan that corrects anything wrong or moved, adds missing steps, flags steps that would break an existing test or documented invariant, and keeps the plan's decisions unless the code makes them impossible. Pay particular attention to: `register_worker`, `claim_run`, `update_run`, `heartbeat_run`, `fail_stale_runs`, `queue_flow_run`, `queue_flow_run_service`, `stop_run`, and `ensure_local_worker` in `app/routers/flows.py`; `run_worker` and `execute_job` in `app/flow_worker.py` (task matrix, shared page/staging/run folder/artifacts/resume keys, `_edge_completed_download`); `app/flow_local_runner.py` and `setup.ps1` service/task installation; the SQL-target and publish-folder locks in `app/routers/pipelines.py`; `_worker_readiness`; `app/flow_replay.py` recipe storage; and every test in `tests/test_flows.py` and `tests/test_flow_worker_discovery.py` that encodes single-worker assumptions (list them by name and say which need updating). Keep the original section structure. Do not implement anything. Output the full revised markdown.
