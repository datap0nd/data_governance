# Pipelines edge routing around cards: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: <pending; filled in below under Merge evidence>
- Evidence cutoff (UTC): 2026-09-07
- Tested code revision: working tree on branch
  `claude/pipelines-artifact-overlap-9flcel` on top of
  `28f3d563f3dbdd479cf31c3eda054ab4c3ff346e`; the commit SHA is recorded in
  the PR testing section.
- Environment: Linux 6.18 container, Python 3.11.15, Node 22.22.2,
  Playwright 1.56.1 with Chromium 141.0.7390.37 (synthetic harness only).
- Overall finding: automated checks PASS; synthetic harness PASS (0 crossing
  edges after the change, 10 before); live checks NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Syntax | `node --check app/static/app.js` | branch working tree, Node 22.22.2 | PASS | command exit 0 |
| Edge router | `node tests/test_lineage_edges.mjs` | same | PASS (new multi-column, lane-spreading, fallback and band-helper assertions) | "lineage edge routing tests passed" |
| Lineage display and layers | `node tests/test_lineage_display.mjs`, `node tests/test_lineage_layers.mjs` | same | PASS | console output |
| All CI Node tests | every `node tests/*.mjs` listed in `.github/workflows/tests.yml` | same | PASS, no failures | console output |
| Python suite | `python -m pytest tests -q` | same, Python 3.11.15 | see Merge evidence (result recorded once the run finished) | console output |
| S-01 before | harness `check.mjs all` and `default` on the `main` code (change stashed) | `28f3d56`, Chromium 141 | 10 edges crossed other cards in each mode | [before-all-columns.png](evidence/before-all-columns.png), [before-default-columns.png](evidence/before-default-columns.png) |
| S-01 after | harness `check.mjs all`, `W=1900 check.mjs default`, `TRACE=source-10 check.mjs all` | branch working tree, Chromium 141 | `hits: []` in all three modes; 29 edges/24 cards, 17/17, 10/24 | [after-all-columns.png](evidence/after-all-columns.png), [after-default-columns.png](evidence/after-default-columns.png), [after-traced-lineage.png](evidence/after-traced-lineage.png) |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-01 to T-06 | NOT RUN | No live Metronome instance or real report lineage is available in this environment; the harness uses a fake payload. | Owner updates the app from `main`, hard-refreshes, and walks the cases on a report with upstream systems, Flows and dependency columns. |

## Findings, limitations and retests

- The synthetic payload was designed to contain edges that skip one, two and
  three columns (upstream system to source, Flow to direct source, dependency
  chains). It shows the defect before the change and its absence after, but
  it is not a real report and does not prove behavior on every real layout.
- Edges that share one gap between two cards are spread onto separate lanes
  inside that gap (up to 6px apart). When many edges share a narrow gap they
  sit close together; they remain outside the cards.
- The wider row gap (0.75rem) slightly increases the diagram's height.
- Same-column loops and backward edges keep their previous curves; the
  synthetic layouts produced no such edge crossing a card.

## Merge evidence

Pending until the PR's final CI run finishes; the PR testing section records
the final run URL, tested head SHA and result before merging.
