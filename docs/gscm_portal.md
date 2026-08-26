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
configuration applied. In current GSCM builds the application-level
`gds_bookmark` dataset is normally available as soon as the portal loads. The
scan reads that dataset first and catalogues every record with its tab, folder
path, bookmark id, menu id, scope, and owner metadata. It opens Setting >
Favorite only as a fallback when the dataset has not loaded yet.

The home screen has its own "Favorite" widget, but it lists only the entries a
user has *pinned* (the pin icon on each row, then Save). It is empty for most
users, so reading it finds nothing even for someone with hundreds of bookmarks.
An earlier version of this adapter did exactly that and reported "no bookmarks
found" - a true statement about the wrong panel.

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

## Why discovery reads Nexacro memory

The Favorite grid is virtualized. Its fixed `gridrow_0` through `gridrow_8`
slots are recycled as the user scrolls, so a DOM-only scan cannot treat row ids
or coordinates as bookmark identity. The loaded Nexacro application already
holds the complete source dataset:

```javascript
const ds = nexacro.getApplication().gds_bookmark;
```

`userreportid` is the stable bookmark identity, `publicscope` identifies the
Private/Public/Custom tab, `scope` identifies the module, `menugroupname` and
`menuname` reconstruct the category path, and `userreportname` is the leaf.
This avoids scrolling, expansion, geometry, and concatenated ancestor text.

If a deployment does not expose `gds_bookmark`, the fallback is deliberately
restricted to `TopFrame.Setting1.form.div_favorite.form.grd_bookmark`. It reads
only `GridRowControl` labels in that grid and treats a visible
`treeitembutton` as the folder signal. It never queries the whole page. This
scope matters because reading the global TopFrame container's `textContent`
concatenates labels such as `Biz Info`, `AX`, `SCM`, and `Channel` into one
false bookmark.

Controls are located by visible label when opening a bookmark, but Nexacro's
rendered `:text` / `:icontext` child is promoted to its owning component before
the click. If the DOM click does not fire the component handler, Metronome uses
Nexacro's native `on_fire_onclick` entry point for that exact observed control.
The Setting gear has no text and is found by id shape (`btn_setting`,
`btn_config`, ...). A failed export retry reloads the portal shell so an empty
or stale Favorite grid cannot leak into the next attempt. Every failure includes
a compact screen inventory so a changed control can be diagnosed from evidence.

## Run lifecycle

```
Scan bookmarks                          Run a flow
──────────────                          ──────────
POST /api/flows/sites/{id}/scan         POST /api/flows/{id}/run
  ↓ flow_catalog_scans row                ↓ flow_runs row
worker claims the scan                  worker claims the run
  ↓ flow_gscm.discover_catalog            ↓ flow_gscm.open_bookmark
  read gds_bookmark in memory             open Setting > Favorite, pick the tab
  (open Favorite only if missing)         select the row, press Go >>
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

## Scoped DOM fallback

The memory dataset is the normal discovery path. The fallback handles the two
ways the rendered grid hides bookmarks:

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

A bookmark is stored with the tab, folder path, and name that locate it again:

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

The **folder path is what disambiguates** a repeated name - the same report is
often filed under several folders, and matching on the name alone would
download a different report than the flow was built for. Two rows sharing a
name still get two catalog entries (`Weekly PSI`, `Weekly PSI (2)`), because
`flow_reports` is keyed by (site, name) and would otherwise collapse them.

`favorite_bookmark_id` is stable dataset identity. DOM element ids are recorded
only by the fallback and are never trusted across runs because Nexacro recycles
them as the tree scrolls and re-renders.

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
turn. The two portals are separate sign-ins against the same profile; the
marker files (`.asap_authenticated`, `.gscm_authenticated`) record each.

Readiness is detected per portal: ASAP is up when it renders navigation
anchors, GSCM when the Nexacro component `mainframe.VFrameSet` exists — GSCM
has no anchors at all.

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
| Run fails naming the available bookmarks | The bookmark was renamed or deleted in GSCM | Rescan the GSCM catalog, then repoint the flow |
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
