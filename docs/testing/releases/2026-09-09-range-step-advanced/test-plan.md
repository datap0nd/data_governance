# Range setting on one recorded step: test plan

- Change/PR: expose range authoring in each element step's Advanced settings and remove the consecutive-click prerequisite
- Code baseline: `85fd02482442787002e802e37a21e6432151455d`; final merged behavior must use the tested PR head
- Related report: [test-report.md](test-report.md)
- Intended environment: Windows ARM64, Python 3.13, Node 24, Playwright and a synthetic Chrome page

## Prerequisites and synthetic data

Use the fictional recording-review page with exactly one ISO-week cell click,
plus ordinary navigation and download steps. No real report data is involved.

## Test cases

| ID | Actions | Expected result | Evidence |
| --- | --- | --- | --- |
| UI-01 | Open Review recording, select the sole week-cell click, expand **Advanced**, and inspect **This is a range step**. | The setting is visible and enabled for the selected element step. It does not ask for or depend on a second week click. | Synthetic browser assertion and owner-approved clickable preview. |
| UI-02 | Enable **This is a range step**, inspect the Range card and configuration, choose element-box level 2, and save. | Only the selected action becomes `select_range`; its ISO start is the selected week, the end remains `latest_selectable`, and the definition becomes v3. | Synthetic browser assertion and saved in-memory definition. |
| UI-03 | Resize the review to 390×844 after enabling the setting. | Advanced, range configuration, save, and recovery controls remain reachable without horizontal overflow. | Synthetic browser assertion. |
| MODEL-01 | Convert a definition containing only one targeted week action, then restore it by clearing the setting. | The model creates one range action, retains the original action for recovery, and restores that action without changing neighboring steps. | JavaScript model assertions. |
| COPY-01 | Copy a saved v3 recording containing a range step through **Choose from template**, edit one display label, save the copied draft, and validate the submitted definition. | The copied draft remains version 3, retains `select_range`, and passes definition validation instead of reporting that range steps require recorder version 3. | Focused synthetic browser/JUnit assertion. |
| SAVE-01 | Submit a definition marked version 2 that contains `select_range` to the recording revision save route, then read and validate the stored revision. | The authoritative save route promotes the definition to version 3 before validation and storage; it never overwrites a range definition with version 2. | Focused route-level pytest/JUnit assertion. |
| REG-01 | Validate the saved v3 definition and run the existing range playback suite in final-head CI. | Definition validation and established week traversal, selection verification, and download gating remain unchanged. | Focused validation plus authoritative final-head CI. |
| FAIL-01 | Inspect Advanced on a navigation or wait step that has no element target. | The setting remains visible but cannot be enabled, and explains that an element target is required; the draft is preserved. | Synthetic/editor contract inspection. |

## Automated checks and acceptance

Run the single affected synthetic browser test once with JUnit output. Separately
run JavaScript syntax, the recording model assertions, and `git diff --check`.
Final-head CI is authoritative for the full Python regression. Accept when the
approved journey, focused checks, and final-head CI pass and the PR is merged to
`main`. On failure, use Undo or clear the range setting to recover the original
recorded action; reverting the PR restores the previous authoring control.
