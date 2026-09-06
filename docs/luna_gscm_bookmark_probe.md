# Luna: 10-minute GSCM bookmark investigation

Copyable instruction: **Read this file and carry out its 10-minute investigation using your work-PC computer-use tools. Return the evidence table and conclusion here. Do not use Metronome to run the experiment.**

## Your task and limits

You are Luna on the owner's work PC. Investigate whether the owner's bookmark in
**GSCM > Setting > Public** can be selected/opened through GSCM's own application
logic without first scrolling its virtualized row into view. The owner can click
it manually after scrolling; recorded playback cannot reach it. The older
Metronome bookmark method was flaky and sometimes fell back to scrolling.
Do not assume that old method, or `set_rowposition()` alone, works.

Start a wall-clock timer now. Stop investigation at 9 minutes and deliver by
10 minutes, including partial results. Use one target and at most three opening
attempts (one manual baseline and two native attempts). Do not spend the budget
installing tools, browsing general documentation, reading the whole repository,
or building automation. Use the existing signed-in browser and your computer-use
skill/tools; obey their restrictions. Browser DevTools inspection and short,
reviewed in-page probes are appropriate only if those tools allow them. If they
do not, report that native inspection is BLOCKED; do not route around the tool
restriction with another execution mechanism.

The owner authorizes navigating Setting/Public, transient selection, and opening
this existing report for this experiment. Do not save/change bookmarks, pin,
delete, change report filters, export/download reports, or run SQL. Do not start
Metronome, its workers, or its old adapter. Do not create a remote-control service,
enable a debugging port, install an extension, disable browser security, capture
credentials, replay HTTP requests, or modify deployed scripts/application code.
If authentication needs the owner, or the target cannot be identified from the
current task/screen, ask one short question and count that time in the budget.

## 0:00-2:00 — identify the target and establish a manual baseline

- Note browser/version if readily available, UTC start time, and target as a
  private local alias such as `Bookmark A`. Use the bookmark the owner is testing;
  do not choose a different report to obtain a pass.
- Open Setting > Public. Observe the starting list position and whether the
  target is rendered. Manual scrolling is allowed here solely to identify the
  target and establish the control case. Note any required folder expansion.
- Use the actual bookmark click and Go sequence. Record whether it opens the
  expected report and what visible title confirms this. No export is needed.
- If the ordinary manual route fails, stop native activation experiments and
  report that failure; there is no working baseline to compare against.

## 2:00-4:00 — inspect the actual grid and activation logic

- Reopen Setting > Public. Through permitted DevTools/computer use, identify the
  exact Favorite Grid, its bound Dataset, the target's stable `userreportid` and
  exact `userreportname`, and the actual Go control. Keep values local/private.
- Inspect only these controls and their handlers. A visible row ID is a recycled
  slot, not bookmark identity. `Setting0`/`Setting1` and frame ownership vary.
  Resolve the observed component path in its real browser frame; do not guess a
  path or assume the top document owns `nexacro.getApplication()`.
- Useful minimal console reads, only in an allowed/observed frame:
  `typeof nexacro !== 'undefined' && typeof nexacro.getApplication === 'function'`
  and, when available, `nexacro.getApplication().gds_bookmark?.getRowCount()`.
  For the experiment use the Grid's actual bound dataset, not an assumed global
  dataset or an index copied from an earlier inventory.
- Read the relevant cell-click and Go handler source, or use a narrowly scoped
  DevTools event breakpoint for the bookmark click. Resume immediately. Determine
  whether Go reads just current dataset/grid selection or also state established
  by the cell-click handler. Do not print whole datasets, application objects,
  request bodies, or unrelated source. If the handler is inaccessible, mark its
  behavior UNKNOWN rather than inventing it.

## 4:00-7:00 — native selection with the target initially unrendered

- Reset through the normal UI to a known list position where Bookmark A is absent
  from the rendered grid. Confirm the Public data is loaded and the target still
  exists exactly once by stable ID with its exact name in the current bound data.
  A clipped existing HTML row and a row absent from HTML are different cases;
  record which is observed. Exclude duplicate-name ambiguity.
- Record before-state: target DOM presence; list scroll position; grid selection
  mode; dataset rowposition; grid current row/cell and selected row(s), as exposed.
  Record any collapsed ancestor separately. Do not alter filters to force a match.
- Inspect the available native methods before invoking them. Test the smallest
  sequence consistent with the observed application and grid mode: for example,
  resolve the target's current data index, set dataset rowposition, and select the
  matching grid row/cell where required. `selectRow` applies to row/multirow modes;
  cell mode differs. Do not treat a return value alone as proof of selection.
- Do not wheel-scroll, set scrollTop, position the grid, expand folders, or click
  the target row during this trial. If Nexacro automatically scrolls or renders
  the row as a consequence of selection, record that precisely; it is not proof
  of activation while the target remains unrendered.
- Confirm which bookmark the actual Go logic will consume. If target identity
  is missing, ambiguous, or inconsistent, do not press Go; record the state.
  Otherwise use the ordinary visible Go button once and check the opened report.
  This is a native-selection + real-Go trial, not a fully background activation.

## 7:00-9:00 — one decisive follow-up

- If the first native trial succeeded, repeat from a different known starting
  scroll position, ensuring the target is initially unrendered again. Prefer a
  collapsed-parent start if that was relevant to the failure. Re-resolve identity
  and the current index; never reuse a recycled element or earlier index.
- If native selection failed because manual click creates additional transient
  state, use the follow-up only for the exact application routine established
  from the observed click/Go source. Inspect its inputs and effects first. Invoke
  it once only if it is clearly limited to selecting/opening this same bookmark
  and the necessary inputs are known. Do not guess private `on_fire_*` signatures,
  manufacture mouse events, patch handlers, or execute unrelated callbacks.
- If that routine cannot be established, use the remaining time for narrow
  inspection, not a scrolling fallback. Report exactly what is unavailable.
- For every attempt distinguish: correct selection, automatic scrolling,
  bookmark row rendering, Go dispatch, and correct report opening. A highlighted
  row, a successful function return, or a closing dialog alone is not a pass.

## 9:00-10:00 — cleanup and return this report

Remove your breakpoints and temporary inspection hooks, resume any paused code,
and leave the browser in a usable state. Do not save portal settings or close
unrelated tabs. Return the findings in this conversation; do not commit evidence
or send messages elsewhere. Keep screenshots/raw source/private identifiers in
approved local storage, if available; use only opaque evidence references here.
Do not include cookies, tokens, private URLs, report data, or raw business titles.

| Attempt | Starting state / target DOM absent? | Exact method sequence (sanitized) | Selection correct? | Auto-scroll / row rendered? | Correct report opened? | Evidence / failure |
| --- | --- | --- | --- | --- | --- | --- |
| Manual baseline | | | | | | |
| Native 1 | | | | | | |
| Native 2 / follow-up | | | | | | |

Finish with:

1. **Verdict:** fully background activation demonstrated / native selection plus
   real Go demonstrated / native selection auto-reveals row / failed / blocked /
   inconclusive. Explain the observed combination rather than forcing one label.
2. **Mechanism:** what the real click changes and what Go consumes; distinguish
   directly observed facts from inference. Give the smallest successful call
   sequence with private values replaced by placeholders, or the exact failure.
3. **Compatibility facts:** browser and Nexacro version if observed, owning frame,
   component path shape, binding, selection mode, tree/index conversion issues.
4. **Limits:** attempts completed, untested states, elapsed time, cleanup status,
   and one next diagnostic if necessary. Two successes establish feasibility,
   not production reliability. Use NOT RUN/BLOCKED for unperformed trials.

## Optional references — only if needed within the timebox

- [Local GSCM adapter documentation](gscm_portal.md): historical context only.
- [Nexacro tree navigation examples](https://docs.tobesoft.com/developer_guide_nexacro_n_en/1420bd57c8db36f0).
- [Nexacro Grid reference](https://docs.tobesoft.com/reference_guide_nexacro_n_v24_ko/Grid):
  version 24 reference; confirm methods on the deployed build before use.

