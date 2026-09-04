# Plan 5 — Flows list: grouped by source, new columns, Open folder

## Goal

Declutter the Flows tab. Flows are grouped under their source
(**ASAP**, **GSCM**, **Outlook**, **Local**), all groups collapsed by
default. Each row shows: Flow, Source (what it pulls), Owner, Type
(output with an app icon), To (file name or `schema.table`), Schedule,
Last run, Active, and actions including a new **Open folder** button.

## Current state (main @ c527be4)

- Renderer `_flowListHtml(flows, workers, catalog, runs)`
  (`app/static/app.js:10819-10848`), a hand-built `<table class="flow-table">`
  (`style.css:462-476`). Columns: Flow (name + "Owner: X" small), Active
  switch, Source, **Download** (type/count/period + browser mode + output
  storage, three lines), Schedule, Last run, actions
  (Run / Stop / Edit / Delete; handlers `:11881-11889`).
- No grouping, no sorting. The Catalog tab has a collapsible tree
  (`_flowCatalogTreeHtml`, `:11302-11320`) whose open state survives the
  5 s poll via `window._flowsState.openCatalogTopics` (`:11828-11834`).
- Each flow row from the API carries `site_name`, `source_adapter`,
  `report_name`, `owner_name`, `owner_email`, `sql_*`, `file_format`,
  `filename_template`, `outlook_subject_contains`, `local_file_path`
  (`_flow_out`, `app/routers/flows.py:1076-1138`).
- Icons: `alertAssetLogo(kind)` (`app.js:737-741`) has `excel` and `sql`
  SVGs.
- No "open folder" anywhere; browsers cannot open `file://` from an
  `http://` page. The only shell hook is `POST /api/scanner/open-path`
  (`app/routers/scanner.py:1494-1519`, unrestricted, runs `explorer` from
  the service process).
- `tests/test_flows_display.mjs` slices `_flowListHtml` by the markers
  `"function _flowListHtml"` … `"/** The folder a report sits in"`.

## Design

### Grouping model (pure function, testable)

```js
// ── Flows list grouping ──
function _flowSourceGroupKey(flow)   // "asap" | "gscm" | "outlook" | "local" | "web" from flow.source_adapter / source_type
function _flowSourceGroupLabel(key)  // "ASAP" | "GSCM" | "Outlook" | "Local" | "Web"
function _flowGroups(flows, sortState)  // [{key, label, count, active, failing, running, rows: [...]}] in fixed order ASAP, GSCM, Outlook, Local, Web; empty groups still rendered (collapsed) so the page shape is stable
```

The adapter → label mapping mirrors `flow_paths.SOURCE_FOLDERS` (plan 1).
Until the backend exposes it, keep the map in JS; when plan 1 lands,
`GET /api/system/paths` returns it and the JS reads it from the flows
bundle.

### Row model

```js
function _flowRowModel(flow, runs)  // one flat object per row, used by both rendering and sorting (plan 6)
// { id, name, groupKey, source: {primary, secondary}, owner, ownerEmail,
//   type: {kind: "sql"|"excel"|"csv"|"html"|"text"|"file", label}, to, toKind: "table"|"file",
//   schedule: {label, next}, lastRun: {status, at}, enabled, running, pathStatus, scriptStatus, layoutOk }
```

- **Source** column content (replaces today's mixed Source cell):
  - ASAP/GSCM: report or bookmark name (`report_name`); secondary line the
    site's category path when known (`automation.category_path`), else
    none.
  - Outlook: `Subject contains "…"`.
  - Local: file name; secondary line the folder relative to the flows
    root.
- **Owner**: `owner_name` or "No owner" (muted); tooltip email.
- **Type**: icon + label. `sql` when `sql_handoff_enabled`; otherwise by
  `file_format` / `asap_download_type` → `excel`, `csv`, `html`, `text`.
  Reuse `alertAssetLogo("sql"|"excel")`; add a small `csv` and `file`
  glyph to that map (inline SVG, same 16 px box). Text label always
  present ("Do not communicate status through color alone" —
  `PRODUCT.md`).
- **To**: SQL → `schema.table` (uppercase rule applied for display when
  `sql_uppercase`); files → the rendered filename template with tokens
  left as-is (`weekly_{week}.csv`); Outlook/Local → `{original}` shown as
  "original name".
- **Schedule** and **Last run** as today; **Active** switch moves after
  Last run so the eye reads left-to-right from identity to state.
- Row badges (small, after the name): "Outside flows root" (plan 1),
  "Folder missing" (plan 3), "Script stale" (plan 4), "Remote target".

### Markup

One `<table class="flow-table flow-table-grouped">` so column widths stay
aligned across groups. Each group is a `<tbody class="flow-group" data-group="asap">`
whose first row is the header:

```html
<tr class="flow-group-row">
  <th scope="rowgroup" colspan="9">
    <button type="button" class="flow-group-toggle" aria-expanded="false" aria-controls="flow-group-asap">
      <span class="flow-group-arrow" aria-hidden="true">▸</span> ASAP
      <span class="flow-group-meta">4 flows · 3 active · 1 failing</span>
    </button>
  </th>
</tr>
```

followed by the flow rows with `hidden` toggled by the button. Using
`<tbody>` + button instead of `<details>` keeps a single table (columns
line up; sortable headers apply to all groups) and remains keyboard
accessible (`Enter`/`Space` on the button). The Catalog tree keeps its
`<details>`.

Open state: `window._flowsState.openFlowGroups` (Set) mirrored to
`sessionStorage["flows.openGroups"]`, restored on render and on the poll
re-render (same approach as `openCatalogTopics`). Default: all collapsed.
A group containing a **running** or **failed** flow renders a count chip
in its header so a collapsed group never hides a problem.

### Actions column

Buttons in order: **Run** / **Stop** (as today), **Open folder**, and an
overflow `…` menu (`.flow-row-menu`, a small popover reusing the
`task-modal` focus helper `_flowBindDialog`) with Edit, View script (plan
4), Expanded logs (latest run), Delete. Keeping Edit and Delete in the
menu halves the button clutter; Delete stays labeled and confirmed.

### Open folder

Backend `POST /api/flows/{id}/open-folder`:

- Resolves `flow_folder` (plan 2) and asserts it is inside the root
  (plan 1). 404 when the folder is missing (with `folder_state`).
- If the request comes from the server machine
  (`app/local_access.is_server_machine(request.client.host)`), launches
  Explorer **in the interactive session**: Metronome runs as a service in
  session 0, so `explorer` from the process is invisible. Reuse the
  pattern from Outlook flows: a per-call scheduled task
  `Metronome_Open_Folder` created with `schtasks /Create /IT /RU <user>`
  once by `setup.ps1` and run with `/Run` after writing the path to
  `<flows_root>\.metronome\open_folder.txt` (the task runs a tiny
  PowerShell `Start-Process explorer.exe (Get-Content …)`). This mirrors
  `HEADED_TASK_NAME` in `app/flow_local_runner.py`. Fallback when the task
  is not registered: `subprocess.Popen(["explorer", path])` as the
  scanner endpoint does, plus a response flag `opened_in_session0: true`
  so the UI can explain.
- Otherwise (remote browser) returns `{opened: false, path}`; the UI shows
  a "Copy path" toast with the UNC/absolute path and a hint "Open this
  folder on the BI desktop". Also always shows the path in a tooltip on
  the button.
- Event log entry per call (actor, flow id).

`POST /api/scanner/open-path` is out of scope but should get the same
root restriction later; note it in `SECURITY`-style docs.

### Status strip

Keep `.flow-status-strip` but reduce to: `N flows · N active · N running ·
N failing · N workers online` and the retention sentence moved into the
Paths page help text.

## Step-by-step

1. Backend: `open-folder` endpoint + `setup.ps1` task registration +
   tests (`tests/test_flows.py::test_open_folder_requires_root_and_server_machine`,
   `::test_open_folder_returns_path_for_remote_browsers`, monkeypatching
   `subprocess.run`). Add `category_path` to `_flow_out` (from the
   report's `automation_json`) so the Source cell has its secondary line.
2. JS: new block between markers `// ── Flows list grouping ──` and
   `// ── Flows list rendering ──` with `_flowSourceGroupKey`,
   `_flowSourceGroupLabel`, `_flowGroups`, `_flowRowModel`,
   `_flowTypeCell`, `_flowToCell`; rewrite `_flowListHtml` to consume
   them; keep the function name and the trailing marker so
   `tests/test_flows_display.mjs` still slices (extend that test rather
   than replace it).
3. Group toggle binding in `_bindFlowWorkspace` (`app.js:11816`), state
   persistence, poll-safe re-render.
4. Actions column, overflow menu, Open folder handler with the
   copy-path fallback (`navigator.clipboard.writeText` with a `<textarea>`
   fallback because the app may be served over plain http).
5. CSS: `.flow-table-grouped`, `.flow-group-row`, `.flow-group-toggle`
   (full-width, left-aligned, focus ring per DESIGN.md), `.flow-type-cell`
   (icon + label, `gap: var(--space-xs)`), `.flow-row-menu`. Density stays
   compact (0.82 rem body).
6. Tests: `tests/test_flows_grouped_display.mjs` (groups in fixed order,
   collapsed by default, counts, running/failing chips, Type/To cells for
   sql/excel/csv/outlook/local, Open folder button present with the path
   tooltip, badges for outside-root/stale script); add to
   `.github/workflows/tests.yml`. Keep `test_flows_display.mjs` assertions
   (no Freshness column).
7. Docs: README flows paragraph on the list layout; screenshot not
   required.

## Risks

- **Session 0 Explorer.** Handled by the scheduled-task launcher with a
  flagged fallback. Verify on the appliance during rollout.
- **Poll re-render flicker** when toggling groups: the render must be
  diffed by group state before replacing `innerHTML` (compare a hash of
  the flows payload and skip re-render when unchanged, which the list
  does not do today).

## Acceptance criteria

- Opening Flows shows four collapsed group headers with counts; expanding
  ASAP lists only ASAP flows with the new columns.
- Open folder opens Explorer on the BI desktop and shows a copyable path
  elsewhere; paths outside the root are refused server-side.
- Existing row actions (Run/Stop/Edit/Delete/Active) keep working and the
  `.mjs` tests cover the new cells.
