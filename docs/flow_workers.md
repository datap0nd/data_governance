# Flow worker capacity

For global Chrome/Edge selection and the new ASAP/GSCM recorded method, see [recorded Flows](recorded_flows.md). The existing behavior below applies to catalog/bookmark Flows.

System > Flow workers configures 1–5 background slots and 1–5 visible (headed)
slots independently; each mode defaults to 1. Claims are serialized in a
SQLite write transaction, so downloads, catalog scans and final processing
cannot oversubscribe the limit. Lowering capacity only blocks new claims.
An assigned worker can still reconnect to its current operation.

The screen shows configured and online slots separately. Online means a recent
worker heartbeat; it does not establish that its portal SSO session is valid.
The watchdog starts missing configured background services. Start background
workers performs the same check immediately and reports each slot's result.
Headed runs and scans start their configured interactive tasks on demand.
Start visible workers also opens those windows explicitly. Windows use the
signed-in BI desktop session and close after work ends and 60 seconds idle.
Helpers stay available while a coordinator prepares the run or waits for SSO.

Run `setup.ps1` on the BI desktop after adding background slots. It reads the saved setting.
`setup.ps1 -FlowHeadlessSlots 3` explicitly saves and installs capacity 3.
New services need the Windows account password during interactive setup;
unattended updates preserve existing credentials and leave uninstalled slots
offline until manual setup. Every profile is authenticated independently.
No browser cookies, live profiles or private recovery stores are copied.

The headed-parallel update registers all five interactive tasks without needing
extra service passwords. After that update, changing headed capacity takes
effect on the next launch without rerunning setup. Interactive setup signs in
the configured profiles; `setup.ps1 -FlowHeadedSlots 3` saves visible capacity 3
and prepares those three profiles. A newly enabled profile may still prompt for
its own SSO sign-in when the Flow runs.

| Slot | Service | Worker ID | Profile suffix |
| --- | --- | --- | --- |
| 1 | MXFlowsWorker | bi-desktop-headless | .metronome-flow-browser |
| 2–5 | MXFlowsWorker2–5 | bi-desktop-headless-2–5 | .metronome-flow-browser-2–5 |
| Headed 1 | Metronome_Flows_Headed task | bi-desktop-headed | .metronome-flow-browser-headed |
| Headed 2–5 | Metronome_Flows_Headed2–5 tasks | bi-desktop-headed-2–5 | .metronome-flow-browser-headed-2–5 |

Each profile has its own download staging and replay cache. New managed flows
use the shared artifact store. Legacy Resume and SQL Retry keep their original
store identity checks and may need the producing slot. Slot 1 retains its
original service, profile and logs.

Setup waits for **all installed slots** to stop before replacing code, including
slots above the current capacity. It preserves unused services/profiles, marks
unused services as manual start and restarts installed configured slots. Both
manual and unattended updates use this same setup path. Registration checks
cover all restarted slots. App auto-updates also wait for active runs and scans.

For manual troubleshooting, `tools/run_flow_worker.ps1 -Slot 2` selects slot 2's
ID and profile, while `-Headed -Slot 2` selects visible slot 2. Stop the corresponding
service or interactive task before starting its manual worker; the profile lock refuses concurrent
use. Flow/scan Stop targets the assigned process, or that exact fixed service
when no process ID is available. An unknown worker is never mapped to slot 1.

## Parallel downloads within a Flow

The builder's Parallel downloads setting is 1–5, default 1. More than one
requires a managed Flow folder. Both headed and headless browsers support it.
Local files, Outlook and SQL-only retries stay sequential. System > Flow workers
also has a limit per portal, default 5; the global limit always applies.

A parent run occupies one slot throughout download and final processing. That
coordinator can download one export itself; other free slots help with the same
bundle. For example, global capacity 3 and per-flow capacity 3 allow one
coordinator and two helpers. A lower portal limit reduces the available helpers.
Workers only help runs matching their browser mode. For a visible check, save
headed capacity 3 under System > Flow workers, then save Browser mode Headed
and Parallel downloads 3 in a Flow containing at least three exports or periods.
Run it and complete sign-in in each window if prompted. The run log identifies
the worker for each export. Three slots are a ceiling: fewer independent exports,
busy workers or a lower portal limit can reduce actual concurrency.
Scans count against both global and portal limits. Reducing a limit lets active
work finish and restricts subsequent claims.

Tasks keep the serial export/link/period order and full-bundle filename index.
Each attempt writes to its own folder under the parent's `.tasks` directory.
After every task succeeds, the coordinator verifies all identities, files and
checksums, assembles them in order, and runs the usual publication,
transformation and SQL stages. Saved SQL and transformation flags still apply;
parallelism never skips or independently runs these stages per download.

Run logs show completed/total exports, active slots and each task's state,
worker, attempt and error. A task lease lasts 90 seconds and renews during work.
Expired leases and replaced workers cannot publish late results. A task failure
stops new claims and drains the others; it does not automatically retry a whole
run. Existing bounded retries inside an individual portal download still apply.

Stop fences all tasks and targets every assigned process. Until Windows confirms
the stop, a task acknowledges cancellation or its lease expires, the parent
retains its reservation. With an unknown process ID, the parallel stop path
waits for acknowledgement/expiry instead of guessing a service. Completed files
remain available for Resume and the source runs stay pinned against retention.
Resume rechecks checksums and downloads missing or changed exports again.
Retry SQL requires a complete downloaded bundle and validated saved inputs.

An interrupted finalizer after SQL begins can have an unknown commit outcome.
The Flow then blocks Run, Resume and Retry SQL. Inspect and reconcile the actual
SQL target before choosing More > Acknowledge SQL reconciliation. This clears
the block and records the acknowledgement; it does not change data or launch a
run. There is no automatic replay or claim of exactly-once SQL across a crash.

Standalone launchers use the same execution stages and saved flags, with local
logs and process locks. They execute downloads sequentially without the server
task pool. See [standalone execution](flow_standalone.md).

## Upgrade and rollback

Workers advertise the task protocol and shared store identity. Old workers
cannot claim parallel parents or tasks. Headed parallel jobs additionally require
the headed task capability, so workers from before this update cannot claim them.
Before reverting the headed-parallel merge, drain headed runs, set headed
capacity to 1 and save headed flows with Parallel downloads 1. The additional
interactive tasks and profiles can remain installed and unused.
New schema is additive, and existing
flows keep parallelism 1. App updates wait for active download tasks as well as
runs/scans. Stop or drain all task runs before reverting the parallel-download
merge; also reset per-flow parallelism to 1 before rolling back so any old code
queues only serial jobs. Revert dependent changes in reverse order and retain
the database and artifact folders. A code revert cannot undo published files or
SQL commits.

Synthetic tests exercise the queue, leases, concurrent downloads, file validation
and final processing. Portal concurrency tolerance, SSO in each installed profile
and actual SQL execution still need verification on the BI desktop.
