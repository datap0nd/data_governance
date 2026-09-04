# Plan 6 — Sortable columns in the Flows list

## Goal

Every column header in the grouped Flows table sorts the rows when
clicked (ascending → descending → default), within each source group,
with the sort remembered for the session and reachable by keyboard.

## Current state (main @ c527be4)

- The Flows table has no sorting; it renders flows in API order
  (`ORDER BY updated_at DESC, name`, `app/routers/flows.py:1913`).
- The app already has a sortable/filterable table engine:
  `dataTable(tableId, columns, rows, opts)` (`app/static/app.js:1428`),
  `_filterAndSortDT` (`:1445`), `_renderDT` (`:1486`, renders
  `th.resizable.sortable` and a filter row), `bindDataTables` (`:1534`),
  `_saveDTState/_loadDTState` (`:1414/:1421`, `sessionStorage`). It
  renders one flat `<tbody>` and owns the whole table markup, so it does
  not fit the grouped `<tbody>` layout from plan 5 without changes.
- Header CSS for sortable columns exists (`style.css:1018-1037`) and
  should be reused for visual consistency.

## Design

### Sorting model (pure, testable)

```js
// ── Flows list sorting ──
const FLOW_SORT_COLUMNS = {
  name:     { label: "Flow",     value: r => r.name.toLowerCase() },
  source:   { label: "Source",   value: r => r.source.primary.toLowerCase() },
  owner:    { label: "Owner",    value: r => (r.owner || "￿").toLowerCase() },   // no owner sorts last
  type:     { label: "Type",     value: r => r.type.kind + "|" + r.type.label.toLowerCase() },
  to:       { label: "To",       value: r => r.to.toLowerCase() },
  schedule: { label: "Schedule", value: r => r.schedule.nextTs ?? Number.MAX_SAFE_INTEGER },  // manual last
  lastRun:  { label: "Last run", value: r => r.lastRun.at ? -Date.parse(r.lastRun.at) : Number.MAX_SAFE_INTEGER },
  active:   { label: "Active",   value: r => (r.enabled ? 0 : 1) },
};
function _flowSortState()                       // {key, dir: "asc"|"desc"|null} from window._flowsState.sort or sessionStorage
function _flowSortRows(rows, state)             // stable sort; null dir = API order; ties broken by name
function _flowNextSortState(current, key)       // asc -> desc -> null
```

`_flowGroups` (plan 5) calls `_flowSortRows` per group, so groups keep
their fixed order and only rows move.

Value functions read the `_flowRowModel` fields (plan 5), so sorting
never parses rendered HTML. `Schedule` sorts by the next occurrence
timestamp (`next_run_at`), then by type label; `Last run` newest first
on the first click because that is the useful direction (the "asc" for
this column is defined as newest-first; the header arrow still shows
the direction).

### Header markup and behaviour

```html
<th scope="col" aria-sort="ascending">
  <button type="button" class="flow-sort" data-sort="owner">
    Owner <span class="flow-sort-arrow" aria-hidden="true">▲</span>
  </button>
</th>
```

- `aria-sort` is set only on the active column (`ascending` /
  `descending`), removed elsewhere.
- Click and `Enter`/`Space` (native button) cycle the state; the table
  re-renders through the normal `_flowShowView("list")` path so the poll
  and the group toggles stay consistent.
- The active state is stored in `window._flowsState.sort` and
  `sessionStorage["flows.sort"]`; restored on page entry.
- Reuse `.sortable` header styling from `style.css:1018-1037` for the
  hover/arrow look; add `.flow-sort` (reset button styles, full cell hit
  area, visible focus ring).
- The Actions column has no button and no `aria-sort`.

### Interaction with grouping and polling

- Sorting does not expand or collapse groups.
- The 5 s poll re-render uses the stored sort state, so rows do not
  jump back to API order.
- When a sort is active, the status strip shows "Sorted by Owner ▲ ·
  Reset" with a reset link (clears to API order).

## Step-by-step

1. JS block between markers `// ── Flows list sorting ──` and
   `// ── Flows list grouping ──` with the table above and the three
   functions; wire `_flowGroups` to use `_flowSortRows`.
2. Header rendering in `_flowListHtml`: generate `<th>` from
   `FLOW_SORT_COLUMNS` plus the Actions header so labels and keys cannot
   drift.
3. Binding in `_bindFlowWorkspace`: delegate click on `.flow-sort`,
   update state, persist, re-render.
4. CSS additions.
5. Tests `tests/test_flows_sorting.mjs` (pure functions: each column
   asc/desc, stable ties, no-owner last, manual schedule last, newest
   run first, state cycling, `aria-sort` only on the active header,
   groups unaffected); add to `.github/workflows/tests.yml`.
6. Docs: one sentence in the README flows section.

## Risks

- **Rendering cost**: the list is small (tens of flows); sorting per
  render is negligible. Keep the sort inside `_flowGroups` so there is a
  single code path.
- **Confusing "asc" for dates**: mitigated by the arrow and the strip
  text ("newest first").

## Acceptance criteria

- Clicking any header sorts rows inside every group; a third click
  restores API order; the state survives the poll and navigation within
  the session.
- Keyboard users can reach and activate the sort buttons; screen readers
  get `aria-sort`.
- The `.mjs` test covers every column's comparator.
