# Metronome Flows redesign — plan set

Eight implementation plans, one per requested change, written against
`main` at commit `c527be4` (`Merge pull request #51 …`). Each plan is
self-contained enough to hand to a separate engineer or AI session, but the
plans share a few decisions that must stay consistent. Those shared
decisions are listed here so a reviewer of any single plan can see the
whole picture.

| # | Plan | File | Depends on |
|---|------|------|------------|
| 1 | Paths settings page and the flows-root sandbox | `01_paths_settings_and_sandbox.md` | — |
| 2 | Auto-create a flow's folder on creation | `02_flow_folder_on_create.md` | 1 |
| 3 | Fixed per-flow layout: `Downloads/` and `Scripts/` | `03_fixed_flow_layout.md` | 1, 2 |
| 4 | Standalone runnable script copy per flow | `04_standalone_flow_script.md` | 1, 2, 3 |
| 5 | Flows list: grouped by source, new columns, Open folder | `05_flows_list_redesign.md` | 1, 2 (for Open folder) |
| 6 | Sortable columns in the Flows list | `06_sortable_columns.md` | 5 |
| 7 | Flow builder redesign: collapsed, visual steps | `07_builder_redesign.md` | 2 (folder no longer entered) |
| 8 | Parallel runners: 1 headed + up to 5 headless, per-flow fan-out | `08_parallel_runners.md` | 1, 3 (shared artifact root) |

Recommended delivery order: 1 → 2 → 3 → 5 → 6 → 7 → 4 → 8. Plans 5–7 are
frontend-heavy and can proceed in parallel with 3 and 4 once plan 2 has
landed the `flow_folder` column and the derived target folder.

## Shared decisions

1. **One configurable root.** `flows_root` is a new `app_settings` key
   (default `<ProjectDir>\metronome\flows`, bootstrapped by env
   `DG_FLOWS_ROOT` and by `setup.ps1`). Everything a flow reads or writes
   lives under it. Plan 1 defines it; every other plan consumes it through
   `app/flow_paths.py`.
2. **Source folders are fixed names.** `flow_sites.adapter` maps to a
   folder: `asap_portal → ASAP`, `gscm_portal → GSCM`,
   `outlook_attachment → Outlook`, `local_file → Local`. A manual
   `web_export` site maps to `Web`. The mapping lives in one place
   (`flow_paths.source_folder_name`) and is reused by the list grouping in
   plan 5.
3. **A flow's folder is server-owned state.** Plan 2 adds
   `flows.flow_folder` (absolute path) and derives
   `flows.target_folder = <flow_folder>\Downloads`. The builder stops
   asking for a target folder (plan 7). Renaming a flow renames the folder
   only when no run is active.
4. **Layout is versioned.** `<flow_folder>\flow.json` records
   `layout_version`, flow id, name, source, and the config hash the
   standalone script was generated from. Plan 3 owns this file; plan 4
   updates it.
5. **The retention root is `Downloads`, not the flow folder.**
   `flow_retention._gate_reason` only deletes direct children of the
   registered storage root, so `register_folder` must be called with the
   `Downloads` folder. Run folders keep the `#<run>_<dd-mm-yyyy>` name.
6. **The private artifact store moves under the root.** Today it is
   `<profile_dir>/run_artifacts/<hash>` and `artifact_store_id` is derived
   from the profile directory. Plans 3 and 8 move it to
   `<flows_root>\.metronome\artifacts\…` so any worker on the machine can
   resume or retry SQL for any run. This is the one change that touches
   existing Direct-file flows' recovery data; plan 3 has the migration.
7. **Standalone scripts are launchers, not forks.** The generated
   `Scripts\run_<slug>.py` imports the installed Metronome code
   (`app.flow_standalone`) and runs the flow's frozen job with the server
   replaced by local no-ops. It does not duplicate the ASAP/GSCM driver
   code. Plan 4 explains why and what "runnable without Metronome" means
   in practice (server down: yes; code checkout deleted: no).
8. **Parallelism is capacity + tasks.** Plan 8 keeps the existing
   claim/heartbeat protocol, adds a worker pool (N headless services,
   one headed task), global caps (headed ≤ 1, headless ≤ 5), and a new
   `flow_run_tasks` table so one run's downloads can be spread across
   workers. Transform + SQL still happen once, after the whole bundle is
   complete, exactly as today.

## Conventions every plan follows

- Python 3.13, FastAPI, raw sqlite3 with idempotent `MIGRATIONS` in
  `app/database.py`. New tables/columns go there.
- Frontend is `app/static/app.js` (vanilla, no build). New UI logic is
  written as top-level `function _flowXxx(...)` blocks between comment
  markers so `tests/*.mjs` can slice and `vm.runInContext` them, and each
  new `.mjs` test is added to `.github/workflows/tests.yml`.
- Delivery follows the repo's `CLAUDE.md`: implement on a `claude/…`
  branch, run the affected pytest files plus `node --check
  app/static/app.js`, merge to `main`.
- Nothing in these plans deletes user files. Folders are created,
  renamed (when safe), or left in place with a marker. The only deletion
  site remains `flow_retention.execute_ops`.

## Open questions surfaced by the plans

These are decisions the owner should confirm; each plan states the
assumption it proceeds under.

- **Local-file sources outside the root (plan 1).** The request says only
  files inside the flows folders may be read or written. Existing
  From-file flows point at UNC shares. The plan assumes the source file
  must be under `<flows_root>\Local\…` and offers a per-flow "external
  read-only source" exception as a follow-up.
- **"Open folder" from a remote browser (plan 5).** Metronome runs as a
  Windows service in session 0. Explorer launched from there is not
  visible. The plan uses an interactive scheduled task for the server
  machine and a "Copy path" fallback for other machines.
- **Existing flows with folders outside the root (plans 1, 2).** The plan
  never moves files. It flags such flows as "needs relocation", blocks new
  runs until an owner relocates them from the builder, and provides a
  one-click "adopt current folder" migration for flows that are already
  inside the root.
