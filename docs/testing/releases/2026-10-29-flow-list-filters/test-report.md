# 2026-10-29: Flows list filters and no sub-groups: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-29 07:13
- Tested code revision: `a9d9905` (application and test changes; this report
  and its screenshots are committed after it)
- Environment: Linux 6.18 (cloud container), Python 3.13.17 checkout-owned
  `.venv` with the locked CI dependencies, Node v22.22.0, Playwright 1.62.0
  with its bundled Chromium 153.0.8010.12 (synthetic, headless)
- Overall finding: every case in the plan passes locally (automated,
  synthetic fixtures). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| F-03, S-01, R-01 | The `python tools/check.py verify` command in the plan | `a9d9905`, Linux above | PASS: 55 passed, 0 failed, 0 skipped, 2 third-party deprecation warnings, 133.6 s; syntax clean for `app/freshness.py` and `app/routers/flows.py` | `.test-runs/20261029T071016998Z-4247-fead9f9f/result.json` |
| L-02, F-07, R-01 | `for test_file in tests/test_*.mjs; do node "$test_file"; done` and `node --check` on every `app/static/*.js` | `a9d9905`, Node above | 26 of 27 Node test files pass, including the new `test_flow_list_filters.mjs`; every script passes `node --check`. `test_gemini_extension.mjs` cannot start here: the `pg` package from the integration's npm install is missing. It fails the same way on unchanged `main`; CI installs it | Console output |
| L-01, F-01 to F-06 | `python -m pytest tests/test_flow_list_browser.py`, with a scratch pytest plugin (not committed) that launches the container's Chromium in place of `channel='chrome'`, because Google Chrome is not installed here | `a9d9905`, Chromium above | PASS: 3 passed, 23.8 s; no page errors | `evidence/flow-list-asap-open.png`, `evidence/flow-list-filtered.png`, `evidence/flow-list-no-match.png`, `evidence/flow-list-narrow.png` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression, browser suite with Chrome | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Usability evidence

Synthetic screenshots at `a9d9905`, 1440 × 1080 unless noted:

- [ASAP opened, no sub-groups](evidence/flow-list-asap-open.png): both
  formerly grouped ASAP Flows are listed directly under ASAP.
- [Database = meto_db](evidence/flow-list-filtered.png): "Showing 1 of 4
  production flows" and the **Clear filters** button.
- [No match](evidence/flow-list-no-match.png): the empty state names the
  recovery.
- [390 px wide](evidence/flow-list-narrow.png): the filters wrap, and the
  table scrolls inside its frame.

Labels are the visible filter names. Feedback appears beside the controls:
the status line and the source headings' counts. The filters survive
navigation and reload, and Clear filters is the next action whenever filters
hide Flows. Before the fix, the first browser run found two defects, and both
were fixed before `a9d9905`. **Clear filters** showed while hidden, because
the button style overrode `hidden`. The dropdowns' accessible names included
their selected option text.

## Findings, limitations and retests

Tests removed with the feature: `tests/test_flow_topic_groups_browser.py`.
It exercised the sub-group controls this change removes (create, edit,
ungroup, Run group). Its harness now lives on as
`tests/fixtures/flow-list/` for the new browser test. The groups API tests
in `tests/test_flow_topic_groups.py` and the static preview test
`tests/test_flow_topic_groups_preview.py` still run unchanged. The preview
passed locally with the same Chromium plugin: 3 passed.

Two existing Node tests were adjusted, and neither lost coverage.
`test_flow_activity_polling.mjs` runs `_flowPatchActivity` in isolation, so it
now stubs the new `_flowVisibleFlows` helper the same way it stubs the
function's other dependencies. In `test_flows_display.mjs`, the "no `Next `
label" check now covers the list below the filters, because the Scheduled
filter offers "Next 3 days". The check that a hidden `next_run_at` value never
appears still covers the whole page.

"Scheduled" answers whether a Flow has a trigger on that Dubai date, whether
the trigger time is still ahead or has already passed today. "Next 3 days" is
today, tomorrow and the day after. The list's dates are computed when the
page loads, so a page left open past midnight in Dubai shows the previous
day's dates until it is reloaded.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
