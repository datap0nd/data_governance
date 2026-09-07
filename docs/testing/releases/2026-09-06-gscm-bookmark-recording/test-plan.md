# GSCM bookmark target recording: test plan

- Change: exact GSCM Favorite target metadata for recorded clicks, scoped dataset resolution, rendered-row fallback through the grid scrollbar, worker capability gate, portable execution, and editor repair.
- Revision: pending final PR head.
- Prerequisites: a worker advertising `gscm_bookmark_targets_v1`; a signed-in work-PC GSCM session; a disposable Public bookmark with a known report title. Keep IDs, URLs, reports and exports private.

| ID | Steps | Expected result / evidence |
| --- | --- | --- |
| BK-01 | Record Setting → Public → bookmark → Go. Select **Bookmark in GSCM Favorite list**, enter its exact name, save and validate. | Repair creates a revision and the recorded page/frame and preceding scope actions stay intact. Capture sanitized UI screenshot and revision ID. |
| BK-02 | Leave the name blank, then enter an incorrect name and retest. | Inline name feedback prevents save; lookup failure names the current scope and asks for repair without changing tab, folder, or opening a report. |
| BK-03 | Test initially visible first, middle and last bookmarks from varied current scrollbar positions. | Current-scope identity is exact, a normal rendered row click is used, and the next recorded Go opens the intended report. Capture private title/ID evidence. |
| BK-04 | Test an off-screen target, delayed rendering, a stalled scrollbar and a collapsed ancestor. | The runner waits for observed changes, reacquires the row, stops at deadline/stall, and reports collapsed ancestry as actionable. No wheel replay, forced click, cached row, or unrelated expansion. |
| BK-05 | Test duplicate names with and without a stable ID, changed order, wrong frame, cancellation, and partial native effects. | Duplicate/missing identity fails closed; ordering does not matter; cancellation/deadline persists; unqualified native selection is never attempted. |
| BK-06 | Run ordinary recorded clicks, downloads and a portable Flow on a compatible worker and attempt claim with an old worker. | Existing behavior remains intact; portable execution includes the helper; old worker cannot claim a bookmark-target recording. |

Automated checks: `python -m pytest tests/test_recording_gscm_bookmark.py tests/test_recording_clicks.py tests/test_flow_recordings.py -q`, affected browser UI test, then the full Python suite. Run frontend syntax/browser checks for the editor. Cleanup: close only the test dialog; do not alter bookmarks or publish/download report data.

Native selection is **NOT RUN** until the separate work-PC investigation has a manual baseline plus two successful native selection + ordinary Go trials from different starting positions. It is an evidence-gated optimization, not this release's fallback.
