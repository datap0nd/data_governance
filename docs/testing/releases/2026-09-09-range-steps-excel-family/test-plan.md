# Semantic week ranges and Excel-family downloads: test plan

- Change/PR: recording definition v3, semantic weekly range playback, and seven Excel-family extensions
- Code baseline: `ed28744e875d48b661575f526035090cbf768f4a`; final merged behavior must use the tested PR head
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows ARM64 with Python 3.13, Node 24, Playwright and synthetic Chromium/Chrome pages

## Prerequisites and test data

Use the fictional range-editor preview and synthetic weekly calendars only. The
calendar fixtures must contain ISO weeks, explicit selected/disabled states and
either an internally scrollable box or deterministic previous/next buttons. Use
generated minimal OOXML workbooks and the repository's sanitized BIFF/XLSB
fixtures. Do not retain downloaded fixture files after the test process exits.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| UI-01 | Open the fictional recording review containing three consecutive week clicks. Select a week click and choose **This is a range**. Inspect the proposed element box, change its parent level, save, then resize to 390×844. | The clicks become one Range card. Start is the earliest recorded ISO week; end is **Newest selectable week**; the saved definition is v3 and contains a stable container locator rather than coordinates or a fixed child list. The narrow view has no horizontal overflow. | Synthetic browser assertion and owner approval in the task. |
| RANGE-01 | Play a range whose start is partly selected, whose box contains later unselected weeks, and whose newest future week is disabled. Repeat across an ISO-year boundary. | Only unselected eligible weeks at or after the start are clicked; the disabled week is excluded; every expected week is selected and the verified end is the newest enabled week. | Focused pytest/JUnit. |
| RANGE-02 | Exercise an internally scrolling virtualized calendar and a paginated calendar with validated previous/next controls. Force DOM repaint between views. | Playback returns to the earliest boundary, reacquires cells after each repaint, reaches the newest page, and verifies a consecutive unique range. A stalled control fails before downloading. | Focused pytest/JUnit. |
| RANGE-03 | Exercise `checked`/ARIA state, an explicit validation-proven class and native **Select all**. Start with already-selected cells. | Playback never clicks an already-selected cell. Select all is only an optimization; cell-level verification remains authoritative. | Focused pytest/JUnit. |
| RANGE-04 | Supply duplicate/missing weeks, week-only labels with ambiguous years, an unexpected selection before start, no observable selected state, cancellation and a navigation timeout. | Each case fails closed with a specific diagnostic and no download action is reached. Cancellation stops before changing the first cell. | Focused pytest/JUnit. |
| COMPAT-01 | Validate v1, v2 and v3 definitions and attempt to claim v3 work with old, v2-only and v3-capable workers. | v1/v2 remain readable and unchanged. Only a worker advertising `recorded_flows_v3` claims v3 recording/run work. Portable execution sources include the range implementation. | Focused pytest/JUnit. |
| XLS-01 | Process `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, `.xlt`, `.xltx` and `.xltm` files through recorded raw-download and normalized-table paths. | The actual workbook container is detected, the compatible browser extension is preserved, and processing produces normalized CSV metadata when requested. `output.format: "xlsx"` remains valid for the family. | Focused pytest/JUnit. |
| XLS-02 | Process prefixed OOXML, corrupt/opaque Excel-suffixed bytes, encrypted-container mismatch, an Excel-named sign-in page and `.xla`/`.xlam`/`.xll` add-ins. | Leading transport bytes do not hide valid ZIP workbooks. Corrupt, encrypted, mislabeled, sign-in and executable add-in inputs are rejected with precise diagnostics; VBA is never executed. | Focused pytest/JUnit. |

## Automated checks

Run the smallest affected Python set once after implementation, producing a
JUnit file under ignored `test_reports/`. The command uses the repository's
Windows pytest symlink workaround and covers range runtime/editor, definition
capabilities and Excel-family processing. Run Python compilation, JavaScript
syntax, the JavaScript model assertions and `git diff --check` separately.
Final-head CI is authoritative for the full Python regression.

## Acceptance and cleanup

Accept when the approved authoring journey remains intact, all focused and
syntax checks pass, required final-head CI passes, and the tested PR is merged
to `main`. Existing v1/v2 recordings remain readable; a
recording edited into a range becomes v3 and must be tested again or explicitly
saved without testing. On regression, disable the v3 worker capability and
revert the merged change. Test processes clean their temporary fixtures; keep
only the committed fictional preview and release documents.
