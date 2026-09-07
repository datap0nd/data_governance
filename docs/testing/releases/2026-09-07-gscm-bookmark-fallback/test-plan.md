# GSCM bookmark fallback navigation and import suggestion: test plan

- Change/PR: top-first scrollbar sweep with observed-change waits for recorded GSCM Favorite bookmark clicks, folder-confirmed duplicate handling, cancellation/deadline heartbeats, activation of repaired recycled-row steps, import-time bookmark suggestion with recorded text reuse, editor name prefill, and bookmark strategy logging in run diagnostics. PR link is recorded in the report.
- Code baseline: the previous bookmark release merged as `28f3d563f3dbdd479cf31c3eda054ab4c3ff346e` ([PR #82](https://github.com/datap0nd/data_governance/pull/82)); deployed app and worker must be updated to this PR's merge SHA.
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows work PC with a signed-in GSCM session for live cases; any OS with Python 3.11+ and Playwright Chromium for automated cases.

## Prerequisites and test data

Update the app and one worker from `main`. Use a disposable Public bookmark
with a known report title, a Favorite scope long enough to scroll, and, for
duplicate cases, two bookmarks sharing a name in different folders. Keep
bookmark names, IDs, URLs, report data and exports private; evidence is the run
ID, the sanitized run log and the log's `bookmark` block.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| FB-01 | Record Setting → Public → bookmark → Go where the recorder captured the row text. Import. | The click step already shows **Bookmark in GSCM Favorite list** with the recorded name; no repair prompt; activation validates. | Revision ID, sanitized editor screenshot. |
| FB-02 | Record the same journey where the recorder captured only a `gridrow_N` locator. Import, open the step. | The step asks for the exact bookmark name with the Favorite-list wording; choosing the target kind prefills any recorded text; saving with an empty name is refused; activation succeeds once a name is entered. | Revision ID, screenshot. |
| FB-03 | Validate with the target scrolled **above** the current view (scroll the list down manually before recording the Setting step, or start from a prior run's position). | Log shows `top_established: true`, `movement: true`, strategy `favorite-scrollbar`; the row is clicked and the recorded Go opens the intended report. | Run ID, log `bookmark` block, private report title reference. |
| FB-04 | Validate first, middle and last bookmarks from varied starting positions, including an initially visible target. | Visible target logs `favorite-visible-row` with no movement; others sweep from the top and stop at the target; Go opens the intended report each time. | Run IDs and log blocks per trial. |
| FB-05 | Target inside a collapsed folder (leave the recorded folder click out). | Failure names the bookmark, says it was not rendered between the top and the end, and points to the collapsed ancestor; no unrelated folder is expanded, no report opens. Reason `end_confirmed`. | Run ID, failure message. |
| FB-06 | Two same-named bookmarks in different folders, target repaired with its stable ID. | Identity resolves; the row is accepted only when its visible folder confirms the target (`folder_confirmed: true`); Go opens the intended report. | Run ID, log block. |
| FB-07 | Two same-named bookmarks in one folder. | Fails closed before any scrolling with the shared-folder message; reason `duplicates_share_folder`. | Run ID, failure message. |
| FB-08 | Cancel the test during the sweep. | The run stops within one movement; no further scrollbar presses; the cancellation outcome is unchanged from ordinary steps. | Run ID, log timing. |
| FB-09 | Wrong frame or Favorite list not open (skip the Setting click). | Fails with the dataset-unavailable message; no whole-page search, no tab change. | Run ID, failure message. |
| FB-10 | Ordinary recorded clicks, a download recording and a portable Python export on the updated worker; claim attempt from a worker without `gscm_bookmark_targets_v1`. | Unchanged behavior; the portable bundle contains the helper; the old worker cannot claim the bookmark-target recording. | Run IDs, bundle listing. |

## Automated checks

```
python -m pytest tests/test_recording_gscm_bookmark.py tests/test_flow_recordings.py tests/test_recording_diagnostics.py tests/test_recording_clicks.py -q
python -m pytest tests -q
node --check app/static/flow_recording_editor.js
node tests/test_recording_visual_model.mjs
```

Synthetic fixtures simulate a recycled, virtualized grid: targets above and
below the view, delayed rendering, a stalled scrollbar, a confirmed end,
duplicates with and without a distinguishing folder, cancellation through the
progress channel, the deadline, a missing scrollbar and an unavailable dataset.
They are not evidence of live portal behavior.

## Acceptance and cleanup

Accepted when automated checks pass on the final head and FB-03/FB-04 succeed
live with the recorded Go opening the intended report. Native selection stays
NOT RUN and disabled. Close only the test dialog; do not alter bookmarks or
publish report data. Rollback: revert the PR merge; recordings keep their
`bookmark_target` metadata and validate against the previous helper.
