# 2026-10-29: Flows list filters and no sub-groups: test plan

- Change/PR: branch `claude/dazzling-meitner-9a2bxn`; PR link in the PR's
  testing section. Two changes the owner asked for in this task:
  1. **No sub-groups.** Opening a source (ASAP, GSCM, Outlook, Local, Python,
     Web) lists every one of its Flows directly. The topic groups added on
     2026-09-13, the **Group** button, **Run group** and **Edit group** are
     gone from the page. Flows that were in a group are listed with the rest
     of their source. Saved groups stay in the database (no data is deleted);
     the groups API remains but nothing on the page calls it.
  2. **Filters.** Next to **User**, five filters narrow the list:
     **Source**, **Active** (active or inactive), **Last run** (succeeded or
     failed), **Scheduled** (today, tomorrow or the next 3 days) and
     **Database** (each SQL database Flows write to, or no SQL output). The
     server reports, for each Flow, which of today, tomorrow and the day after
     in Dubai have a trigger. A paused or manual Flow has none. Choosing a
     filter opens the sources with a match. The status line reads "Showing N
     of M" while filters hide Flows, and **Clear filters** resets every filter,
     including User. The browser tab remembers the filters.
  3. **Live while open** (from the Codex review). **Last run** follows each
     Flow's latest finished run. Starting a run keeps its Flow in view, and
     when a run finishes and moves a Flow into or out of the selected result,
     the list re-renders. Each Flow carries the Dubai date its scheduled days
     count from, and the activity poll reports the server's current Dubai
     date. When that date moves on, the list reloads the scheduled days once.
- Code baseline: `main` at `44301b8`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux with the checkout-owned Python 3.13 `.venv`,
  Node 22 and a synthetic headless browser; final-head CI on Ubuntu and
  Windows.

## Prerequisites and test data

Disposable fixtures only: temporary SQLite databases, fictional Flows and a
fixed clock (Thursday 29 October 2026, 10:00 in Dubai). The browser cases run
the real Flows list page and HTTP handlers through
`tests/fixtures/flow-list/`. They use four Flows:

- **ASAP daily sales:** active, daily, last run succeeded, writes to `postgres`.
- **ASAP weekly stock:** paused, Fridays, last run failed, writes to `meto_db`.
- **Web movement:** active, Fridays, no SQL.
- **Local snapshot:** manual, no SQL.

Both ASAP Flows are saved in an old topic group named "ASAP tracker".

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| L-01 | Open the list; expand ASAP | All four Flows are on the page; both ASAP Flows show directly under ASAP; no topic heading, Group, Run group or "ASAP tracker" control; "4 production flows"; Clear filters hidden | `test_flow_list_browser.py::test_sources_list_every_flow_without_sub_groups`, `evidence/flow-list-asap-open.png` |
| L-02 | Every Flow in the tab renders exactly once without filters; Draft Flows stay in the Draft tab | One row per Flow; no topic markup | `test_flow_list_filters.mjs` |
| F-01 | Source = ASAP, then Active = Inactive, then Clear filters | ASAP opens and shows its two Flows ("Showing 2 of 4"); then only the paused Flow; Clear restores all four, resets every filter, hides itself and leaves focus on Source | `test_flow_list_browser.py::test_each_filter_narrows_the_list_and_clearing_restores_every_flow` |
| F-02 | Last run = Failed, then Succeeded | Only the failed Flow, then only the succeeded Flow | same test |
| F-03 | Scheduled = Today, Tomorrow, Next 3 days | Today: the daily Flow. Tomorrow and Next 3 days: the daily Flow and Web movement. The paused and manual Flows never appear | same test; `test_flow_triggers.py::test_flow_list_reports_scheduled_days_for_active_scheduled_flows_only` |
| F-04 | Database lists only databases in use; choose each | Options: All databases, meto_db, postgres, No SQL output; each shows its own Flows | same browser test, `evidence/flow-list-filtered.png` |
| F-05 | A combination with no match (Source = Local, Database = meto_db), then Clear filters | "No production flows match these filters" with "Clear filters to see every Flow."; Clear brings all four back | same browser test, `evidence/flow-list-no-match.png` |
| F-06 | Choose Database = postgres, reload; narrow to 390 px | The filter and "Showing 1 of 4" survive the reload; no horizontal page scroll; every filter visible | `test_flow_list_browser.py::test_filters_survive_reload_and_fit_a_phone_width`, `evidence/flow-list-narrow.png` |
| F-08 | With Last run = Failed open: another Flow's run fails; then a failed Flow is run and succeeds | The new failure joins the list on the next poll; the running Flow stays in view in Flows in execution; its success takes it out of Failed; the filter stays selected | `test_flow_list_browser.py::test_last_run_filter_follows_runs_that_finish_while_the_page_is_open`; `test_flow_list_filters.mjs` |
| F-09 | Activity reports a later Dubai date than the loaded Flows | One reload of `/api/flows` updates every Flow's scheduled days, even with repeated polls; the list re-renders only when a Scheduled filter is chosen; the same date, or no date, reloads nothing | `test_flow_list_filters.mjs`; `test_flow_triggers.py::test_flow_list_reports_scheduled_days_for_active_scheduled_flows_only` |
| S-01 | Scheduled days from triggers: daily, weekly, monthly day 31, several triggers, Dubai midnight | Today counts after its trigger time has passed; Friday is tomorrow on a Thursday; day 31 is the day after tomorrow on 29 October; triggers combine; 21:30 UTC on Thursday is Friday in Dubai | `test_flow_triggers.py::test_scheduled_days_follow_dubai_dates_across_every_trigger` |
| F-07 | Filter rules, stored values and live counts | Each filter and combination selects the expected Flows; a stored value no longer offered (for example a retired database) falls back to all; live heading counts use the same tab, user and filters | `test_flow_list_filters.mjs` |
| R-01 | Existing Flows list, triggers, handover snapshot and groups API behavior | Unchanged | remaining `test_flow_triggers.py`, `test_flow_topic_groups.py`, `test_flow_target_roundtrip.py`, `test_flow_handover.py`, every other `tests/test_*.mjs` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_flow_triggers.py --test tests/test_flow_topic_groups.py \
  --test tests/test_flow_target_roundtrip.py --test tests/test_flow_handover.py \
  --syntax app/freshness.py --syntax app/routers/flows.py
for test_file in tests/test_*.mjs; do node "$test_file"; done
for source_file in app/static/*.js; do node --check "$source_file"; done
python -m pytest tests/test_flow_list_browser.py
```

The browser test launches Chrome (`channel='chrome'`) like the other browser
suites; final-head CI runs it with Chrome. Required final-head CI
(`Merge ready`) is the full regression.

## Usability evidence

The owner specified both changes in this task. The changed controls are the
five filter dropdowns, **Clear filters**, the source headings without
sub-groups, the "Showing N of M" line and the no-match state. Each is walked in
L-01 and F-01 to F-06, including the recovery from a no-match state, with
screenshots in `evidence/`.

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
Rollback: revert the PR. Saved topic groups are untouched, so a revert shows
them again. Nothing to clean up.
