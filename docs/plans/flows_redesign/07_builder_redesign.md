# Plan 7 — Flow builder redesign: collapsed, visual steps

## Goal

Replace the long flat form with a stepped builder: each section is a
collapsible step with a status, only one step is open at a time, the
summary rail stays visible, and users no longer choose a folder (plan 2
derives it). The result must still submit the same `FlowWrite` payload
minus `target_folder`.

## Current state (main @ c527be4)

- Full in-page view (`_flowShowView("builder", existing)`, `app.js:11468`),
  reached from the source picker (`_flowSourcePickerHtml`, `:10798`) with
  three cards: Outlook, From file, Website report.
- Two builders: `_flowOutlookBuilderHtml` (`:11051-11118`, also used for
  From file) and the portal `_flowBuilderHtml` (`:11183-11277`) with a
  two-column `.flow-builder-shell` and a sticky `aside.flow-summary`
  "Execution contract" (`:11273-11276`).
- Portal builder sections in order: Start from an existing flow; Source
  and report (site, report, Scan report); Report filters; Export views;
  Download links; Download behavior (period strategy, file grouping,
  download type, ASAP checkboxes, Excel pre-processing, browser mode,
  week range, weeks per download, Output storage, **Target folder**,
  Filename template); Transformation; Ownership; Schedule and SQL. All
  are flat `.flow-form-section` blocks (`style.css:544-548`); visibility
  uses the `hidden` attribute.
- Submit: `_flowCollectBuilder` (`:11611-11706`) builds the payload,
  `apiPut`/`apiPostJson`, `toast`, `navigate("flows")`.
- Dynamic behaviour helpers: `syncAsapDownloadControls` (`:12074-12140`),
  `syncLocalFileWorksheet` (`:12051`), report scan polling
  (`:11933-11988`), replicate settings (`:11908-11930`), transform Browse
  (`:12181-12201`).
- `DESIGN.md` asks for "full-page numbered steps with one visible working
  section and a persistent summary rail on wide screens", and "disable
  dependent fields until their parent selection is complete".

## Design

### Step structure (both builders share it)

| # | Step | Contents | Complete when |
|---|------|----------|---------------|
| 1 | **Source** | Source cards with icons (ASAP, GSCM, Outlook, Local) replace the current three; then per source: website + report/bookmark picker with search and folder tree (reuse `_flowCatalogTreeHtml` in a compact selector), or subject substring, or file path + worksheet. Flow name lives here (pre-filled from the report/bookmark name, editable). | name set and source resolved |
| 2 | **What to download** | Portal only: filters, export views / download links, period strategy, grouping, download type, ASAP checkboxes, Excel pre-processing, weeks. Presented as three sub-cards: *Filters*, *Exports*, *Periods*. | required selections valid (live check via existing validation messages) |
| 3 | **Where it goes** | Read-only computed path `ASAP \ <Flow name> \ Downloads` (updates as the name changes; after save shows the absolute folder and an Open folder button), Output storage as two illustrated radio cards (Run folders / Direct files), Filename template with a live preview of the rendered name (`_flowFilenamePreview`) and token chips that insert on click. No Target folder input. Browser mode moves here as "Run visibly on the BI desktop" toggle with a one-line explanation. | template valid for the selected grouping |
| 4 | **After download** | Transformation (optional; upload lands in the flow's `Scripts` per plan 3) and SQL handoff (append/replace, database/schema/table with the catalog `datalist`, uppercase). Off by default; step shows "Off" in its status when unused. | valid or off |
| 5 | **Schedule and owner** | Schedule type/time/days/day and Owner select with the failure-alert explanation. | owner chosen (or explicitly "no owner") |

Header of each step: number, title, one-line status summary
("ASAP · Sell-out weekly · 3 exports", "Off", "Not set"), and a chevron.
Exactly one step is open; clicking another header opens it and
collapses the current one. Steps that are not applicable to the source
(step 2 for Outlook/Local) are hidden entirely, and the numbering
compresses (1, 2, 3, 4).

Edit mode opens with all steps collapsed and the summary rail filled, so
an owner sees the whole configuration at a glance and expands the one
they want to change.

### Markup and state

```html
<section class="flow-step" data-step="source" data-state="complete|incomplete|invalid|off">
  <h2 class="flow-step-header">
    <button type="button" class="flow-step-toggle" aria-expanded="true" aria-controls="flow-step-source-body">
      <span class="flow-step-number">1</span>
      <span class="flow-step-title">Source</span>
      <span class="flow-step-status">ASAP · Sell-out weekly</span>
      <span class="flow-step-chevron" aria-hidden="true"></span>
    </button>
  </h2>
  <div class="flow-step-body" id="flow-step-source-body"> … existing controls … </div>
</section>
```

- `_flowStepState(form)` returns `{source, download, destination, after, schedule}` with
  `{state, status}` computed from the same values `_flowCollectBuilder`
  reads, so the header text and the payload can never disagree.
- Opening/closing is a single `_flowOpenStep(key)` that sets `hidden` on
  the other bodies and `aria-expanded`; there is no free-form accordion
  library.
- Error placement: server errors from `.flow-form-error` are mapped to a
  step by field prefix (`target`→destination, `sql_`→after,
  `schedule_`/`owner_`→schedule, else source/download); that step opens
  and shows the message inside; the global error line stays for
  unmapped messages.
- Submit button label stays "Create flow" / "Save changes" and is
  disabled while any step is `invalid`; a "Next" button at the bottom of
  each open step advances to the next visible step.
- The summary rail (`aside.flow-summary`) keeps the execution-contract
  entries and gains the resolved folder path and the Type/To pair from
  plan 5, so the rail and the list say the same thing.

### Visuals

- Source cards: icon (reuse `alertAssetLogo` map plus new `outlook` and
  `folder` glyphs), name, one-line description, disabled state with reason
  ("No enabled website with discovered reports") for portals.
- Output storage as radio cards with a tiny two-line diagram each
  (`#123_04-09-2026\` folders vs exact filenames).
- Filename preview line under the template input, rendered with the
  current week/date so `{week}` reads as `202636`.
- Everything stays within DESIGN.md: no nested bordered cards inside the
  step body; dividers between sub-cards; 0.82–0.88 rem controls; visible
  focus rings.

### Payload changes

- Remove `target_folder` from the payload (server ignores it after plan
  2; drop it from the client once plan 2 is merged).
- Everything else unchanged so `tests/test_flows.py` payload contracts
  hold. `_flowCollectBuilder` is split into `_flowCollectSource`,
  `_flowCollectDownload`, `_flowCollectDestination`, `_flowCollectAfter`,
  `_flowCollectSchedule`, merged by `_flowCollectBuilder` — each is
  slice-testable.

## Step-by-step

1. Extract the shared step shell: `_flowStepHtml(key, number, title, bodyHtml)`,
   `_flowStepState`, `_flowOpenStep`, `_flowStepStatusText`, in a block
   between `// ── Flow builder steps ──` and the existing
   `_flowOutlookBuilderHtml` marker.
2. Rebuild `_flowOutlookBuilderHtml` and `_flowBuilderHtml` on the step
   shell without changing control ids (`#flow-site`, `#flow-report`,
   `#flow-sql-enabled`, …) so the existing `sync*` handlers and scan
   polling keep working. Remove `#flow-target-folder`; add
   `#flow-destination-path` (read-only) and `#flow-filename-preview`.
3. New source picker with four cards; map ASAP/GSCM cards to the portal
   builder with `#flow-site` preselected (the site list already
   distinguishes adapters).
4. Error-to-step mapping; Next buttons; disabled submit while invalid.
5. CSS: `.flow-step`, `.flow-step-header`, `.flow-step-toggle`,
   `.flow-step-status`, `.flow-source-card` refresh, `.flow-output-card`.
6. Tests: `tests/test_flow_builder_display.mjs` (steps rendered in order,
   only one open, status text per state, no target-folder input, step
   hidden for Outlook/Local, error maps to the right step, collectors
   produce the same payload keys as today for each source type) added
   to the workflow. Keep `node --check`.
7. Docs: README "Create flow" paragraph and `docs/flow_paths.md` note
   that the folder is derived.

## Risks

- **Regression surface** is the biggest: the builder has many coupled
  handlers. Mitigate by keeping control ids and by adding the collector
  tests before touching markup.
- **Screen height**: with one step open the page is shorter than today;
  the rail must not overlap the sticky submit bar on small screens
  (stack below 900 px per DESIGN.md).

## Acceptance criteria

- Creating each source type takes the user through numbered, collapsible
  steps with clear statuses; no folder is typed anywhere.
- Edit mode opens collapsed with accurate status lines.
- Payload parity with today (minus `target_folder`) proven by tests.
