# Flow worker capacity

System > Flow workers configures 1–5 background slots; the default is 1.
Visible debugging has one separate headed slot. Claims are serialized in a
SQLite write transaction, so downloads, catalog scans and final processing
cannot oversubscribe the limit. Lowering capacity only blocks new claims.
An assigned worker can still reconnect to its current operation.

The screen shows configured and online slots separately. Online means a recent
worker heartbeat; it does not establish that its portal SSO session is valid.
The watchdog starts missing configured services. Start configured workers
performs the same check immediately and reports each slot's result.

Run `setup.ps1` on the BI desktop after adding slots. It reads the saved setting.
`setup.ps1 -FlowHeadlessSlots 3` explicitly saves and installs capacity 3.
New services need the Windows account password during interactive setup;
unattended updates preserve existing credentials and leave uninstalled slots
offline until manual setup. Every profile is authenticated independently.
No browser cookies, live profiles or private recovery stores are copied.

| Slot | Service | Worker ID | Profile suffix |
| --- | --- | --- | --- |
| 1 | MXFlowsWorker | bi-desktop-headless | .metronome-flow-browser |
| 2–5 | MXFlowsWorker2–5 | bi-desktop-headless-2–5 | .metronome-flow-browser-2–5 |
| Headed | Metronome_Flows_Headed task | bi-desktop-headed | .metronome-flow-browser-headed |

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
ID and profile, while `-Headed` selects the headed profile. Stop the corresponding
service before starting its manual worker; the profile lock refuses concurrent
use. Flow/scan Stop targets the assigned process, or that exact fixed service
when no process ID is available. An unknown worker is never mapped to slot 1.

The capacity control enables different flows to run at the same time. Per-flow
download fan-out is a separate delivery checkpoint. Revert that checkpoint
before reverting capacity support, after draining active work.
