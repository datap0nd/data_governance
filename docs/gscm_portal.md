# GSCM portal adapter

GSCM (Global Supply Chain Management, `mdscm.sec.samsung.net`) is a **separate
website from ASAP** — different data, different backend, different frontend
framework. It is registered in Metronome as its own `flow_sites` row with the
`gscm_portal` adapter and shares nothing with ASAP beyond the generic Flows
plumbing: the worker loop, the download staging monitor, filename rendering,
Excel→CSV normalization, and the SQL handoff.

## Why the adapter looks nothing like the ASAP one

| | ASAP | GSCM |
|---|---|---|
| Client | HTML in an iframe | TOBESOFT Nexacro (`Nexacro17`/`21`) |
| Discovery unit | Report menu tree | Bookmarks in the Setting dialog |
| Where filters live | Metronome, per report | Inside GSCM, saved into the bookmark |
| Period selection | Sell-out Week prompts | None - the bookmark holds it |
| Export | Export Wizard, CSV or XLSX | One MDI toolbar button, XLSX only |
| Files per run | One per export view x period | One |

## Where the bookmarks actually are

Not on the home screen. GSCM keeps them in a modal:

```
Top nav gear -> Setting
  |
  +- Favorite          <- left rail (Layout / Dashboard / Installation below it)
       |
       +- [MX v]  ( Private | Public | Custom )      <- scope dropdown + tabs
       |
       +- SCM                                        <- folder tree
       |    +- Actual Sales
       |    |    +- MENA_Actual_sales                <- a bookmark
       |    |    +- MX B2B Actual Sales
       |    +- B2B Biz Plan
       |         +- B2B BO Fcst V2
       |
       +- [ Go >> ] [ Save ] [ Close ]
```

Select a row, press **Go >>**, and GSCM opens that report with its saved
configuration applied. The scan walks this exact route the way a flow run
does — gear, Setting, Favorite, one scope tab at a time with the run's rebind
retries — and stops where a run would press **Go >>**: it reads each activated
tab's bookmarks and lists them. Activating the tab matters because GSCM loads
a scope's rows on selection; the application-level `gds_bookmark` dataset read
at portal load holds only what has loaded so far, and trusting it without the
walk is what once reported a user's Public bookmarks missing. Once a tab is
active, the dataset is authoritative for tab, folder path, bookmark id, menu
id, scope, and owner metadata when the runtime exposes it. The rendered
Favorite grid is also inventoried whenever it binds, then explicitly
reconciled with those dataset records for discovery. It is never an execution
identity fallback when the dataset is unavailable.

## Release note: deterministic bookmark activation

GSCM flow runs now fail closed around the exact Favorite identity. A runnable
catalog entry must contain both the stable `userreportid` and the raw
`userreportname`. The raw name is stored separately from the normalized catalog
label: only outer whitespace is ignored, while case, repeated internal spaces,
and every other character remain identity-significant.

At run time Metronome resolves exactly one ID/name row in `gds_bookmark`,
selects it through the bound visible Nexacro Grid, and proves that the dataset
row position, Grid current row, and Grid selection all identify that same row.
The Go handler is invoked only by one browser-side guard that repeats the full
identity and selection check in the same JavaScript operation. There is no
bookmark-label DOM click and no tree name/path fallback. Duplicate IDs,
renames, ambiguous grids, scope drift, inaccessible dataset state, and stale
selection all leave the Favorite dialog open and block export.

Existing ID-less flows, or flows whose saved `favorite_name` was normalized
instead of preserving the portal's raw name, must be rescanned and repointed.

The home screen has its own "Favorite" widget, but it lists only the entries a
user has *pinned* (the pin icon on each row, then Save). It is empty for most
users, so reading it finds nothing even for someone with hundreds of bookmarks.
An earlier version of this adapter did exactly that and reported "no bookmarks
found" - a true statement about the wrong panel.

## The scan runs where the site's flow runs run

Because the scan walks the live portal exactly like a run, it must execute in
the same browser as the runs that are known to work: same worker, same
browser mode, same persistent profile, same signed-in session. Catalog scans
used to be pinned to the headless service worker; on a site whose flows run
headed, that opened a *different* browser with a *different* profile, where
the same Setting-gear click failed ("the Setting > Favorite dialog did not
open"). A GSCM scan job now carries an `execution.browser_mode` derived from
the site's most recent successful run (or, before any run has succeeded, from
any headed flow on the site), and workers only claim scans that match their
own mode. ASAP scans and scan jobs queued before this routing stay headless.

## What the adapter never clicks

The Favorite dialog puts **Save**, **Unselect**, and a per-row **pin** toggle
next to the controls the scan needs. This adapter reads and runs reports; it
never edits what the user has stored. `FORBIDDEN_CLICK_WORDS` is checked
against both the visible label and the component id of every candidate before
any click, in all three paths - label clicks, id-hint clicks, and the tree
sweep - and a label on that list raises rather than clicking. Tests assert that
a full scan and a full run touch neither Save, Unselect, nor a pin.

The tree sweep clicks folder rows only, never report rows.

## Popup cleanup before bookmark clicks

Every existing `clear_screen` call hides the stale wait overlay and inventories
visible Nexacro popup, notice, alert, message, confirmation, and `pdv_`
containers. It closes only an observed Close/X component, promotes rendered
`:text` or `:icontext` children to their owning component, and uses the native
Nexacro click fallback when the DOM click does not dispatch the handler.
Setting > Favorite and the wait window are explicitly excluded, and every
close candidate still passes through `FORBIDDEN_CLICK_WORDS`.

There is no startup delay when no popup is visible. When a popup is found,
Metronome rechecks it for up to two seconds. A popup that remains is tolerated
unless its rectangle overlaps the exact gear, tab, folder, bookmark row, or Go
control Metronome is about to click. Only that proven obstruction stops the
operation; its container id, close-control id, geometry, and screen inventory
are included in the error. Reading `gds_bookmark` remains a direct JavaScript
operation and is not gated on popup state.

## How discovery reads an activated tab

The Favorite grid is virtualized. Its fixed `gridrow_0` through `gridrow_8`
slots are recycled as the user scrolls, so a DOM-only scan cannot treat row ids
or coordinates as bookmark identity. Once a scope tab has been activated, the
loaded Nexacro application holds that scope in its source dataset:

```javascript
const ds = nexacro.getApplication().gds_bookmark;
```

`userreportid` is the stable bookmark identity, `publicscope` identifies the
Private/Public/Custom tab, `scope` identifies the module, `menugroupname` and
`menuname` reconstruct the category path, and `userreportname` is the leaf.
The dataset is the authoritative source for stable ids and scope, but discovery
also waits for and inventories any rendered rows. That second source provides
grid-bound telemetry and lets the scanner explicitly reconcile an id-less grid
observation with exactly one dataset row. Dataset rows remain valid when the
grid never binds; the scan logs that contract failure with grid ids, row counts,
raw scope counts, and Setting-shell state instead of discarding the bookmarks.
Reading the dataset **without** activating the tabs is used only when the
Setting gear cannot be reached at all, and that scan is marked incomplete.

Every grid sweep is deliberately restricted to the Setting dialog's
`div_favorite.form.grd_bookmark` grid (the dialog frame's index varies by build:
`Setting0` on the current portal, `Setting1` on an earlier one, so only the
dialog-local tail is matched). It reads only `GridRowControl` labels in that
grid and treats a visible
`treeitembutton` as the folder signal. It never queries the whole page. This
scope matters because reading the global TopFrame container's `textContent`
concatenates labels such as `Biz Info`, `AX`, `SCM`, and `Channel` into one
false bookmark.

Navigation controls such as scope tabs may be located by visible label; their
rendered `:text` / `:icontext` child is promoted to its owning component before
a click. The bookmark itself is never located or clicked by rendered label.
The Setting gear has no text and is found by id shape (`btn_setting`,
`btn_config`, ...). Every failure includes a compact screen inventory so a
changed control can be diagnosed from evidence.

## Deterministic control resolution

GSCM control lookup is deterministic and effect-verified. Navigation tries
known component paths, frame-number-agnostic DOM shapes, scoped labels, and
guarded positional fallbacks. Execution is stricter: bookmark identity requires
an exact stable ID/raw-name tuple from the application dataset, and Go uses the
live Nexacro component tree only after the atomic selected-grid guard passes.
Rendered tree navigation remains discovery-only.

After the deterministic repair passes a headed live run, the planned
per-profile recipe layer can generalize component-tree lookup and preserve the
last verified strategy as a preference. It will validate loaded component
references, continue through alternatives when a preference fails, and write
back a newly verified fallback. The recipe does not introduce AI into flow
runs.

Scrapling is intentionally not used for this adapter. Nexacro renders
absolute-positioned `div` elements whose ids are full component paths, while
the Favorite grid virtualizes and recycles its rendered row slots. CSS or
HTML-structure similarity therefore has no stable semantic object to match.
The Nexacro component tree and bound datasets expose the actual controls,
records, stable bookmark ids, and native event targets, so they are stronger
and safer primitives than generalized page scraping for this portal.

## Run lifecycle

```
Scan bookmarks                          Run a flow
──────────────                          ──────────
POST /api/flows/sites/{id}/scan         POST /api/flows/{id}/run
  ↓ flow_catalog_scans row                ↓ flow_runs row
worker claims the scan                  worker claims the run
  ↓ flow_gscm.discover_catalog            ↓ flow_gscm.open_bookmark
  open Setting > Favorite, activate       open Setting > Favorite, resolve the
  each tab like a run, read its rows      exact ID/raw-name scope and select it
  (dataset ids/scopes authoritative;
  rendered grid inventoried/reconciled)
  ↓ POST .../scans/{id}/progress          wait for the overlay to settle
flow_reports rows, one per bookmark       ↓ flow_gscm.trigger_excel_export
                                          click btn_exceldown
                                          ↓ Edge native download completion
                                        _store_completed_download (xlsx)
                                          ↓ keeps the workbook, writes a
                                            normalized CSV beside it
                                        transformation (optional) → SQL handoff
```

The workbook is preserved as downloaded; the normalized CSV next to it is what
the SQL handoff inserts. That is the same contract ASAP's XLSX exports use, so
`sql_mode`, `sql_database/schema/table`, and the transformation script hook all
behave identically for GSCM.

GSCM has no ASAP Export Wizard. Its Flow payloads omit the ASAP semantic type,
Export Report Title, and Export filter details fields; the API clears those
fields if an older or copied client submits them. GSCM remains forced to its
single native Excel export.

## Scoped rendered-grid discovery inventory

During discovery, the per-tab dataset read supplies authoritative identity
while the grid sweep supplies rendered-state telemetry and can recover an
id-less catalog observation. This discovery-only sweep handles the two ways the
rendered grid hides bookmarks:

* **The grid virtualizes.** Only rows in view exist in the DOM, so the sweep
  pages the tree down and re-reads until nothing new appears. Because a row's
  parents scroll out of view, the open folder stack is carried from one
  screenful to the next - rebuilding it from the visible rows alone would file
  a row under the wrong folder, or under none.
* **Folders start collapsed.** A collapsed folder looks exactly like a report
  to an indentation-only test. The real signal is the row's
  `treeitembutton`: it is visible for folders and hidden for bookmark leaves.
  Folder rows are clicked to expand, and the sweep repeats until no new rows
  appear.

## Bookmark identity

A bookmark is stored with display metadata plus the exact execution identity:

```json
{
  "kind": "gscm_favorite",
  "category_path": ["Public", "SCM", "Actual Sales", "MENA_Actual_sales"],
  "favorite_tab": "Public",
  "favorite_name": "MENA_Actual_sales",
  "favorite_folder_path": ["SCM", "Actual Sales"],
  "favorite_bookmark_id": "RC_123456",
  "favorite_menu_id": "AS470",
  "favorite_scope": "AS",
  "excel_btn_id": "mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown"
}
```

`favorite_bookmark_id` plus the raw, outer-trimmed `favorite_name` is the runtime
identity. The folder path and synthetic catalog labels such as `Weekly PSI (2)`
are display/discovery metadata only and never authorize a run. DOM row ids are
never trusted across runs because Nexacro recycles them as the tree scrolls and
re-renders.

## Scan replacement behavior

Every successful, non-empty GSCM scan is an authoritative snapshot. Before the
new bookmark list is applied, Metronome removes the prior scan's unreferenced
discovered bookmark rows and their discovered filters. Current bookmarks are
then inserted from the new snapshot instead of accumulating across scans.

A missing bookmark that is still referenced by an existing Flow cannot be
deleted without breaking that Flow. It remains internally as a stale, disabled
tombstone until the Flow is repointed or removed. Failed, cancelled, empty, or
validation-incomplete scans do not replace the last good snapshot. Historical
operation timings are preserved, but their link to a deleted bookmark row is
cleared.

## Authentication

GSCM sits behind Samsung SSO with Knox MFA, the same as ASAP. Metronome
authenticates it the same way it authenticates ASAP: the flow worker owns a
persistent Edge profile, and the profile is signed in once, interactively, in a
visible window:

```
python app/flow_worker.py --profile-dir <profile> \
    --authenticate-url https://mdscm.sec.samsung.net/nexa/index.html \
    --authenticate-adapter gscm_portal
```

`setup.ps1` does this automatically for every enabled portal — `tools/get_flow_auth_url.py`
now prints one `adapter<TAB>url` line per site, and setup bootstraps each in
turn, **for both browser profiles**: the headless service profile
(`~/.metronome-flow-browser`) and the headed on-demand profile
(`~/.metronome-flow-browser-headed`). The two profiles are deliberately
separate (Chromium profiles cannot be shared concurrently), and SSO sessions
live per profile — signing in one does nothing for the other. The two portals
are also separate sign-ins against each profile; the marker files
(`.asap_authenticated`, `.gscm_authenticated`) record each.

Readiness is detected per portal: ASAP is up when it renders navigation
anchors, GSCM when the Nexacro component `mainframe.VFrameSet` exists — GSCM
has no anchors at all.

When a GSCM session expires mid-flight, the worker first recovers it exactly
the way ASAP always has: it fills the encrypted BI-desktop credential (stored
once under Flows > Catalog) into the shared Samsung SSO form and waits for the
Nexacro shell to render. Both portals redirect to the same SSO host, so the
one stored credential serves both.

If MFA cannot be satisfied in the service profile, set the flow's browser mode
to **headed** and complete the prompt in the visible window. A headed run that
lands on the sign-in form does not fail: it posts "GSCM sign-in required" to
the run log and waits up to five minutes for SSO and Knox to be completed in
the visible Edge window, then resumes on its own. A headless run reports the
actionable "GSCM is not signed in" error instead — including when the session
expires mid-flow, which previously surfaced as a misleading "was not on
screen" failure. Every failed run and scan also saves a screenshot of the
live page under the profile's `diagnostics` folder; the error message names
the file.

### The CDP alternative, and why it is not the default

The reverse-engineering notes drive GSCM by attaching Playwright to the
analyst's own Chrome over the DevTools Protocol on port 9222, reusing that
window's live SSO session. That works well for a script run by hand, but it is
the wrong default for Metronome:

* It requires a human-owned Chrome to be running and signed in, so scheduled
  and headless runs cannot work.
* Downloads land wherever that Chrome puts them, outside the staging folder the
  worker monitors — losing the completeness and stall detection that
  `_wait_for_staged_download` provides.
* It would make GSCM the only site in Flows that cannot run unattended.

The persistent worker profile gives the same SSO reuse with none of that. If
GSCM ever refuses to keep a session in the automation profile, CDP attach is
the fallback to add — and it belongs in `flow_gscm`, behind a per-site setting,
not in the shared worker loop.

## Backend routes (reference only)

Metronome never calls these; they are recorded because they explain what the
screen is doing while the adapter waits. GSCM's backend is Spring MVC speaking
Nexacro's **SSV** text serialization — posting JSON to these routes raises a
server-side `NullPointerException`, which is why the adapter drives the browser
instead of the API.

| Endpoint (under `/gscm30/`) | Purpose |
|---|---|
| `common/frame/topframe/permissionmenu.do` | Validates SSO session and user permissions |
| `common/master/tree.do` | Loads the SCM sitemap tree |
| `common/biz/treeconfig/usertreeUserDefinedList.do` | The user's favorites/bookmark layout |
| `common/master/itemfilter/itemFilterStrategicList.do` | Global strategic filter boundaries |
| `common/master/measure/getMeasure.do` | Currency and quantity measures |
| `common/master/defaultdimtype.do` | Default dimension layout |
| `gbm/mobile/sellin/sellinbizplan/selectplanid.do` | SIBP plan interval ids |
| `gbm/mobile/sellin/sellinbizplan/getdynamicplan.do` | Computes SIBP columns for the current filters |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Scan fails: "Setting > Favorite dialog did not open" | Neither the id hints nor the top-bar position found the gear | The `ICON CONTROLS:` section of the `On screen:` inventory lists every text-less control with its id; add the right one to `SETTING_BUTTON_HINTS` |
| Scan fails: "none of its tabs listed a bookmark" | The tree did not render, or the scope dropdown points at an empty business | Read the `On screen:` inventory; check the `MX` dropdown |
| Scan fails: "GSCM did not render its Nexacro client" | The profile has no GSCM session | Re-run the auth bootstrap; try headed mode |
| Run fails with no stable bookmark ID/name | The flow predates exact GSCM execution identity | Rescan the GSCM catalog, then repoint the flow |
| Run fails with exact-name mismatch | The bookmark was renamed, its case/internal whitespace changed, or the catalog association is stale | Rescan the GSCM catalog, then repoint the flow; matching is intentionally case-sensitive |
| Run fails with bound dataset/Grid state unavailable or ambiguous | This Nexacro build does not expose one scriptable visible Favorite Grid, or selection could not be proven | Use the failure state report to verify the Grid binding; this build remains non-runnable until support is reviewed—there is no DOM fallback |
| Run fails with duplicate stable ID or scope drift | Live portal state is internally ambiguous | Leave the dialog open, rescan, and investigate the reported ID/name/scope rows before retrying |
| Run fails: "GSCM popup blocked control" | A visible portal popup survived its Close/X action and overlaps the next bookmark control | Use the reported container and close-control ids plus `On screen` inventory to update the observed id vocabulary; protected Save/Delete-style controls are never clicked |
| Run fails: "Excel export button was not found" | The bookmark opened a screen with no grid export | Open the bookmark by hand and confirm the toolbar offers Excel |
| Run hangs then exports anyway | The wait overlay outlived its query | Expected; the adapter forces the overlay down after its budget |

## Code map

| File | Role |
|---|---|
| `app/flow_gscm.py` | The whole adapter: overlay/popup handling, Setting dialog navigation, tree reconstruction, Go >>, Excel trigger, screen inventory |
| `app/flow_worker.py` | Dispatches scans and runs to the adapter; per-portal auth bootstrap |
| `app/routers/flows.py` | `GSCM_PORTAL_ADAPTER`, discovery gating, GSCM-specific flow validation |
| `app/database.py` | Registers the GSCM site and upgrades a pre-existing one |
| `app/static/app.js` | Adapter-aware catalog and builder (bookmark wording, no filters/period) |
| `tests/test_gscm.py` | Adapter behavior against a fake Nexacro page, plus catalog and flow wiring |
