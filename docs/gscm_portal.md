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
| Discovery unit | Report menu tree | Bookmarks on the home screen |
| Where filters live | Metronome, per report | Inside GSCM, saved into the bookmark |
| Period selection | Sell-out Week prompts | None — the bookmark holds it |
| Export | Export Wizard, CSV or XLSX | One MDI toolbar button, XLSX only |
| Files per run | One per export view × period | One |

Nothing in GSCM is a link, a table, or a form control. Every element is a
Nexacro component whose DOM `id` is its absolute path in the component tree:

```
mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown
```

Three facts follow, and they shape `app/flow_gscm.py`:

1. **Bookmarks are the catalog.** A GSCM user configures a report inside GSCM —
   filters, period, dimensions — and saves it as a favorite on the home
   screen. That is the unit worth automating, so the scan reads the favorites
   list rather than walking a sitemap. A discovered GSCM report therefore
   declares **no Metronome filters**; asking the user to re-pick them here
   would be asking twice.
2. **Ids are matched by shape, not by literal.** The documented favorites path
   contains a layout-dependent segment (`div_section4_MOBILE`). Every lookup
   matches the stable trailing component name (`stc_userreportname`,
   `btn_exceldown`) and only falls back to the full literal path.
3. **The framework fights automation.** Nexacro parks a full-screen wait
   overlay (`mainframe.waitwindow`, z-index 2,000,000) over the page and floats
   un-anchored popup cards above everything. Both swallow clicks and both
   outlive the work that raised them, so every interaction clears them first
   and clicks with `force=True`.

## Run lifecycle

```
Scan bookmarks                          Run a flow
──────────────                          ──────────
POST /api/flows/sites/{id}/scan         POST /api/flows/{id}/run
  ↓ flow_catalog_scans row                ↓ flow_runs row
worker claims the scan                  worker claims the run
  ↓ flow_gscm.discover_catalog            ↓ flow_gscm.open_bookmark
  open portal, clear overlay              double-click the favorite card
  read stc_userreportname labels          wait for the wait overlay to settle
  ↓ POST .../scans/{id}/progress          ↓ flow_gscm.trigger_excel_export
flow_reports rows, one per bookmark       click btn_exceldown
                                          ↓ staged download monitor
                                        _store_completed_download (xlsx)
                                          ↓ keeps the workbook, writes a
                                            normalized CSV beside it
                                        transformation (optional) → SQL handoff
```

The workbook is preserved as downloaded; the normalized CSV next to it is what
the SQL handoff inserts. That is the same contract ASAP's XLSX exports use, so
`sql_mode`, `sql_database/schema/table`, and the transformation script hook all
behave identically for GSCM.

## Bookmark identity

A bookmark is stored with both its container id and its name:

```json
{
  "kind": "gscm_favorite",
  "category_path": ["Favorites", "Biz_trip_GSCM"],
  "favorite_id": "mainframe.VFrameSet.HomeFrame...div_list.form.div_data01",
  "favorite_name": "Biz_trip_GSCM",
  "excel_btn_id": "mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown"
}
```

GSCM renumbers `div_dataNN` when the user reorders their favorites, so a stored
id can silently start pointing at a different report. At run time the **name
wins**: the adapter re-reads the list and resolves the card by name, falling
back to the id. If neither matches, the run fails naming the bookmarks that
*are* on the screen, rather than exporting the wrong report.

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
to **headed** and complete the prompt in the visible window.

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
| Scan fails: "No GSCM bookmarks were found" | The profile is signed in as a different user, or no favorites are saved | Save a favorite in GSCM; re-run the auth bootstrap for `gscm_portal` |
| Scan fails: "GSCM did not render its Nexacro client" | The profile has no GSCM session | Re-run the auth bootstrap; try headed mode |
| Run fails naming the available bookmarks | The bookmark was renamed or deleted in GSCM | Rescan the GSCM catalog, then repoint the flow |
| Run fails: "Excel export button was not found" | The bookmark opened a screen with no grid export | Open the bookmark by hand and confirm the toolbar offers Excel |
| Run hangs then exports anyway | The wait overlay outlived its query | Expected; the adapter forces the overlay down after its budget |

## Code map

| File | Role |
|---|---|
| `app/flow_gscm.py` | The whole adapter: overlay/popup handling, favorites reading, bookmark opening, Excel trigger |
| `app/flow_worker.py` | Dispatches scans and runs to the adapter; per-portal auth bootstrap |
| `app/routers/flows.py` | `GSCM_PORTAL_ADAPTER`, discovery gating, GSCM-specific flow validation |
| `app/database.py` | Registers the GSCM site and upgrades a pre-existing one |
| `app/static/app.js` | Adapter-aware catalog and builder (bookmark wording, no filters/period) |
| `tests/test_gscm.py` | Adapter behavior against a fake Nexacro page, plus catalog and flow wiring |
