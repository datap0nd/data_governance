# Pipelines edge routing around cards: test plan

- Change/PR: Pipelines page connector lines no longer cross the cards of the
  columns they pass through. Every edge that spans more than one column now
  hops each gutter with a smooth curve and crosses every intermediate column
  flat, inside a gap that holds no card (between two cards or below the last
  one). The card row gap grew from 0.35rem to 0.75rem so a line fits between
  two cards. Frontend only: `app/static/app.js`, `app/static/style.css`,
  `app/static/index.html` (cache-busting versions), `tests/test_lineage_edges.mjs`.
- Code baseline: main at `28f3d563f3dbdd479cf31c3eda054ab4c3ff346e` (PR #82).
  Deployed app must serve `style.css?v=58` and `app.js?v=70`.
- Related report: [test-report.md](test-report.md)
- Intended environments: any browser that opens Metronome; the synthetic
  harness runs on Linux/Windows with Node 22 and Playwright Chromium.

## Prerequisites and test data

- Live checks need a Metronome instance updated from `main` with at least one
  report whose lineage has Upstream Systems, Flows and Source Dependencies, so
  the diagram contains edges that skip a column (an upstream system feeding a
  source two or more columns to its right, or a Flow loading a direct source
  while dependency columns sit in between).
- Synthetic checks need only the repository, Node 22 and a Playwright
  Chromium. The fake diagram payload lives in
  [evidence/harness/payload.js](evidence/harness/payload.js); it contains no
  real report, source or portal data.
- Hard-refresh the browser once after updating so the new asset versions load.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Open **Pipelines**, select a report, keep the default columns (Upstream Systems, Flows, Source Dependencies, Sources). | No connector line passes through the body of any card it does not start or end on. Lines that skip a column cross it only through the gap between two cards or below the column's last card. | Screenshot of the diagram |
| T-02 | Enable **Power BI Tables** and **Visuals** with the column toggles. | Same as T-01 with all columns visible; the added adjacent columns keep their single smooth curves. | Screenshot |
| T-03 | Click a source card to trace its lineage, then click empty space to reset. | While traced, off-path cards collapse and the highlighted edges are redrawn without crossing any remaining card; the arrowheads still land on the target card. After reset, the full diagram redraws as in T-01. | Screenshot in traced state |
| T-04 | Hover or keyboard-focus a rerouted (multi-column) edge. | The explanation tooltip still opens along the whole new path, including the flat crossing segments. | Observation |
| T-05 | Resize the window narrower and wider. | Edges redraw and still avoid cards at every width; no horizontal scrollbar appears inside the diagram unless columns overflow as before. | Observation |
| T-06 | Expand a Power BI table card (chevron) so cards below it move down. | Crossing edges follow the moved gaps after the expand transition settles. | Observation |
| S-01 | Synthetic: run the harness (see below) in modes `all`, `default` and with `TRACE=source-10`. | `hits` is an empty array in every mode; before this change the same payload reported 10 crossing edges. | `evidence/*.png`, command output |

T-01 to T-06 require a live Metronome instance with real lineage data. S-01
runs locally from the repository.

## Automated checks

```bash
node --check app/static/app.js
node tests/test_lineage_edges.mjs
node tests/test_lineage_display.mjs
node tests/test_lineage_layers.mjs
python -m pytest tests -q
```

Synthetic browser harness (from the repository root):

```bash
cd docs/testing/releases/2026-09-07-pipeline-edge-routing/evidence/harness
PLAYWRIGHT_MODULE=<path to the playwright package> node check.mjs all after-all.png
PLAYWRIGHT_MODULE=<path to the playwright package> W=1900 node check.mjs default after-default.png
PLAYWRIGHT_MODULE=<path to the playwright package> TRACE=source-10 node check.mjs all after-trace.png
```

Set `CHROMIUM=<path>` when Playwright's bundled Chromium is not installed. The
script prints `edges`, `cards` and `hits`; each `hits` entry names an edge and
the cards its path crosses.

## Acceptance and cleanup

Accepted when every Node test passes, the Python suite passes, and the harness
reports zero hits in all three modes on the tested revision. Live cases T-01 to
T-06 stay NOT RUN until the owner checks them on the updated app. Nothing to
clean up. Rollback: revert the PR; no data or settings change.
