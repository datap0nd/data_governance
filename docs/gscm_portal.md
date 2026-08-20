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
configuration applied. The scan reads all three tabs and catalogues every leaf
row, tagged with the tab and folder path it came from.

The home screen has its own "Favorite" widget, but it lists only the entries a
user has *pinned* (the pin icon on each row, then Save). It is empty for most
users, so reading it finds nothing even for someone with hundreds of bookmarks.
An earlier version of this adapter did exactly that and reported "no bookmarks
found" - a true statement about the wrong panel.

## Why this adapter matches on text and geometry, not on ids

Every id here would have to be guessed. Instead:

* **Controls are clicked by their visible label** - `Favorite`, `Public`,
  `Go >>`. These survive a Nexacro layout change that would break an absolute
  component path. The one exception is the Setting gear, which has no text and
  is found by id shape (`btn_setting`, `btn_config`, ...).
* **The tree is rebuilt from indentation.** The rows carry no DOM nesting;
  depth is expressed purely as horizontal offset, which is also how the screen
  communicates it to a human. A row with a more-indented row beneath it is a
  folder; everything else is a bookmark.
* **Every failure prints what was on screen.** `screen_inventory()` dumps the
  visible labels with their ids and positions into the error and the scan log,
  so one failed run is enough to write an exact selector instead of guessing
  again.

## Run lifecycle

```
Scan bookmarks                          Run a flow
──────────────                          ──────────
POST /api/flows/sites/{id}/scan         POST /api/flows/{id}/run
  ↓ flow_catalog_scans row                ↓ flow_runs row
worker claims the scan                  worker claims the run
  ↓ flow_gscm.discover_catalog            ↓ flow_gscm.open_bookmark
  open Setting > Favorite                 open Setting > Favorite, pick the tab
  read Private/Public/Custom trees        select the row, press Go >>
  ↓ POST .../scans/{id}/progress          wait for the overlay to settle
flow_reports rows, one per bookmark       ↓ flow_gscm.trigger_excel_export
                                          click btn_exceldown
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

A bookmark is stored with the tab, folder path, and name that locate it again:

```json
{
  "kind": "gscm_favorite",
  "category_path": ["Public", "SCM", "Actual Sales", "MENA_Actual_sales"],
  "favorite_tab": "Public",
  "favorite_name": "MENA_Actual_sales",
  "favorite_folder_path": ["SCM", "Actual Sales"],
  "excel_btn_id": "mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown"
}
```

The **folder path is what disambiguates** a repeated name - the same report is
often filed under several folders, and matching on the name alone would
download a different report than the flow was built for. Two rows sharing a
name still get two catalog entries (`Weekly PSI`, `Weekly PSI (2)`), because
`flow_reports` is keyed by (site, name) and would otherwise collapse them.

Element ids are recorded but never trusted across runs: Nexacro regenerates
them as the tree scrolls and re-renders.

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
| Scan fails: "Setting > Favorite dialog did not open" | The gear was not found by id shape | Read the `On screen:` inventory in the scan log and add the real id to `SETTING_BUTTON_HINTS` |
| Scan fails: "none of its tabs listed a bookmark" | The tree did not render, or the scope dropdown points at an empty business | Read the `On screen:` inventory; check the `MX` dropdown |
| Scan fails: "GSCM did not render its Nexacro client" | The profile has no GSCM session | Re-run the auth bootstrap; try headed mode |
| Run fails naming the available bookmarks | The bookmark was renamed or deleted in GSCM | Rescan the GSCM catalog, then repoint the flow |
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
