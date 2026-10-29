# 2026-10-29: Flows list filters and no sub-groups: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-29 07:26
- Tested code revision: `fb32a85` (application and test changes, including
  the fixes for the two Codex review findings; this report update is
  committed after it)
- Environment: Linux 6.18 (cloud container), Python 3.13.17 checkout-owned
  `.venv` with the locked CI dependencies, Node v22.22.0, Playwright 1.62.0
  with its bundled Chromium 153.0.8010.12 (synthetic, headless)
- Overall finding: every case in the plan passes locally (automated,
  synthetic fixtures). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| F-03, F-09, S-01, R-01 | The `python tools/check.py verify` command in the plan | `fb32a85`, Linux above | PASS: 55 passed, 0 failed, 0 skipped, 2 third-party deprecation warnings, 135.0 s; syntax clean for `app/freshness.py` and `app/routers/flows.py` | `.test-runs/20261029T072210840Z-5538-7ebd6c76/result.json` |
| L-02, F-07 to F-09, R-01 | `for test_file in tests/test_*.mjs; do node "$test_file"; done` and `node --check` on every `app/static/*.js` | `fb32a85`, Node above | 26 of 27 Node test files pass, including the new `test_flow_list_filters.mjs`; every script passes `node --check`. `test_gemini_extension.mjs` cannot start here: the `pg` package from the integration's npm install is missing. It fails the same way on unchanged `main`; CI installs it | Console output |
| L-01, F-01 to F-06, F-08 | `python -m pytest tests/test_flow_list_browser.py`, with a scratch pytest plugin (not committed) that launches the container's Chromium in place of `channel='chrome'`, because Google Chrome is not installed here | `fb32a85`, Chromium above | PASS: 4 passed, 46.0 s; no page errors | `evidence/flow-list-asap-open.png`, `evidence/flow-list-filtered.png`, `evidence/flow-list-no-match.png`, `evidence/flow-list-narrow.png` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression, browser suite with Chrome | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Usability evidence

Synthetic screenshots, 1440 × 1080 unless noted. They were taken at
`a9d9905`, and the rerun at `fb32a85` produced byte-identical images:

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
hide Flows. The first browser run found two defects, and both were fixed
before `a9d9905`. **Clear filters** showed while hidden, because
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
today, tomorrow and the day after.

The Codex review of `5a42893` raised two findings, and both were fixed in
`fb32a85` (F-08, F-09):
- The Last run filter did not follow runs that finished while the page was
  open: a new failure never appeared under Failed.
- The scheduled days kept counting from the load date after midnight in Dubai.

A test against the earlier code would fail: the new browser case waits for
the new failure to appear, and the earlier code never re-rendered.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
