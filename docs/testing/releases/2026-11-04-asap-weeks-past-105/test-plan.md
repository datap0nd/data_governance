# 2026-11-04: ASAP week ranges past 105 weeks: test plan

- Change/PR: branch `claude/metronome-asap-data-limit-wulqmq`; PR link in the
  PR's testing section. The owner asked for the 105-week limit to be removed.
  Two limits existed:
  1. **Flow week range.** A Flow refused a Sell-out Week range longer than
     105 weeks ("A flow can cover at most 105 weeks."). A "Start to latest
     available" Flow whose start week was more than 104 weeks before the
     newest discovered week could therefore no longer be saved or queued.
     A scheduled run hit this as an unhandled error. The range now has no
     upper length. "End week cannot be before start week." still applies.
  2. **ASAP slider discovery.** When a report's Week or Period control is a
     two-handle slider, discovery expands its bounds into every week. A
     slider spanning more than 105 weeks produced no weeks at all, so the
     scan dropped the Week filter. Any span is now expanded.

  Unchanged: **Weeks per download** (and the rolling window) stays limited to
  105 weeks per file, and "Start to latest available" still means the newest
  week found by the report's last full ASAP discovery scan.
- Code baseline: `main` at `e8ca974`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux with the checkout-owned Python 3.13 `.venv`;
  final-head CI on Ubuntu and Windows.

## Prerequisites and test data

Disposable fixtures only: temporary SQLite databases with a fictional ASAP
site and report, and a scripted in-memory slider. Nothing reaches a portal or
a SQL server.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| W-01 | Report week options 2024-W01 … 2026-W45 (149 weeks). Save a "Start to latest available" Flow, start 2024-W01, one file every 7 weeks; queue a run | The job covers 2024-W01 to 2026-W45: 22 files, the first 2024-W01–W07, the last 2026-W44–W45, every discovered week exactly once and in order | `test_flows.py::test_start_to_latest_keeps_reaching_the_newest_week_past_105_weeks` |
| W-02 | Save a fixed Flow from 2024-W01 to 2026-W45, 7 weeks per file; queue a run. Then try an end week before the start week | The Flow saves and queues 22 files ending 2026-W44–W45; an end before the start is still refused | `test_flows.py::test_fixed_range_longer_than_105_weeks_is_accepted` |
| W-03 | Discover a Week slider whose bounds are 2024-W01 and 2026-W45 and whose handles start at 2026-W10 and 2026-W45 | Discovery returns all 149 weeks, from 202401 to 202645, with no week 53 in 2024; both handles are restored to 2026-W10 and 2026-W45 | `test_flows.py::test_asap_week_slider_spanning_more_than_105_weeks_reports_its_newest_week` |
| W-04 | Run W-01 to W-03 against the code before this change | All three fail, so they detect the old limits | Recorded in the report |
| R-01 | Existing Flow week planning, ASAP discovery, recorded week/month sliders and recorded week ranges | Unchanged | rest of `test_flows.py`, `test_flow_worker_discovery.py`, `test_recording_sliders.py`, `test_recording_ranges.py`, `test_gscm.py::test_a_gscm_flow_cannot_ask_for_a_week_range_gscm_does_not_expose` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_flows.py --test tests/test_flow_worker_discovery.py \
  --test tests/test_recording_sliders.py \
  --test tests/test_gscm.py::test_a_gscm_flow_cannot_ask_for_a_week_range_gscm_does_not_expose \
  --syntax app/routers/flows.py --syntax app/flow_range_slider.py
python -m pytest tests/test_recording_ranges.py
```

`tests/test_recording_ranges.py` launches Playwright's bundled Chromium; where
the locked Playwright's browser build is not installed, point it at an
installed Chromium for the run. Required final-head CI (`Merge ready`) is the
full regression.

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
After deployment, an affected Flow needs a fresh **Full scan** or report
**Refresh** in Flows > Catalog before its newest week moves, because the end
week comes from the last scan. Rollback: revert the PR; no data or settings
change. Nothing to clean up.
