# Recording journey previews

The current usability checkpoint for recording replacement and template
retargeting is [retarget.html](retarget.html). It uses fictional Country/Main
buttons to demonstrate the visible **Record again** action, the distinction
between a display-only step name and the target used during playback, Undo,
saved versions and immediate replacement recording. Serve `app` locally and
open `/static/recording-preview/retarget.html`.

The current preview is [slider.html](slider.html): a date range control. One
recorded click on a week slider handle (`Click “Week end”`) becomes, through
**Advanced → This is a date range control**, a `set_range` step with its
**Start** and **End** week parameters (portal default, fixed week, newest
selectable week, current or previous week, with a weeks-to-add offset), the
control's value kind (week numbers or dates with a Sunday or Monday week) and
the element box. Production `flow_recording_model.js` and
`flow_recording_editor.js` render it; only the API layer is fictional
(`slider.js`). Serve `app` as below and open
`http://127.0.0.1:8769/static/recording-preview/slider.html`.
`tests/test_recording_slider_editor.py` walks the journey at 1400×1000 and
390×844 and skips when neither the Chrome channel nor the bundled Chromium can
launch.

The previous preview is [python-scripts.html](python-scripts.html): the
**Python scripts** source. It shows the source picker with the new card, the
Python builder (ordered script rows with Add / Remove / Browse, the Output
choice between a final CSV or Excel file and a SQL table with the shared SQL
and materialized-view controls, owner, schedule and email), the Flows list
with its **Python** group, and run history with a failed run whose error names
the failing script and step. Production `app.js` renders every screen; only the
API layer is fictional (`python-scripts.js`). The preview bar switches the save
outcome (saved, or the validation error "Python scripts must be .py files."
shown beside the form with every value preserved). Each script row also has
an optional **Arguments** line (`-sheet`) and a **Values, one per run**
textarea with a live run count, shown in the list as
`fetch_orders.py -sheet (3 values) → clean_orders.py`; the third save outcome,
"Script arguments have an unclosed quote.", focuses the first row's Arguments
field, and run history shows a run in progress on its second value with the
single-script Flow's three deliverables. Serve `app` as below and
open `http://127.0.0.1:8769/static/recording-preview/python-scripts.html`.
`tests/test_python_scripts_preview.py` walks every control at 1280×900 and
390×844, saves screenshots when `PREVIEW_EVIDENCE_DIR` is set, and skips when
neither the Chrome channel nor the bundled Chromium can launch.

The previous preview is [templates-refresh.html](templates-refresh.html):
reusable recordings (**Choose from template** in the recording editor and
**Include its recording** when replicating a flow) and the **Refresh
materialized views** step under SQL handoff, with run history and the
production run-log page showing per-view outcomes and **Retry view refresh**.
Production `app.js`, the recording editor and `flow_run_log.js` render every
screen; only the API layer is fictional. The preview bar switches the
discovery result (three views, verified empty, missing, incomplete, stale,
cyclic) and the retry outcome, and switches the **Send again** outcome of the optional
**Email the final file** step (recipients and subject as the last block of
After download, run history status text, run-log section and recovery). Serve `app` with
`python -m http.server 8769 --bind 127.0.0.1 --directory app` and open
`http://127.0.0.1:8769/static/recording-preview/templates-refresh.html`.
`tests/test_templates_refresh_preview.py` walks every control at 1280×900 and
390×844 and saves screenshots when `PREVIEW_EVIDENCE_DIR` is set. Owner
feedback on this journey is recorded in the
[release report](../../../docs/testing/releases/2026-09-07-reusable-recordings-view-refresh/test-report.md).

The previous preview is [optional.html](optional.html): recording tests start
directly, waits can be inserted, and downloaded-data checks are optional. It
uses the production recording editor/model with fictional API responses.
Select a download to add/remove a minimum-row check; the fixture has three
data rows, so the default four-row check fails until removed or adjusted.
See the [current test report](../../../docs/testing/releases/2026-09-06-optional-recording-checks/test-report.md).

## Historical feedback checkpoints (superseded)

The remaining previews below preserve earlier design iterations. Their report
title/readiness questions no longer describe supported recording behavior.

Local, fictional-data prototype based on repository revision `da4e4511`.
Reuses Metronome's stylesheet, button classes, colors and bundled Outfit font.
This document describes the preview checkpoint before implementation.

Run from the repository root:

```powershell
python -m http.server 8769 --bind 127.0.0.1 --directory app
```

Open http://127.0.0.1:8769/static/recording-preview/index.html.

Walkthrough: Open recording → Start recording → Finish recording → Save draft.
Try Test recording with the completion check empty, then enter “Results updated”.
Test → Back to Edit Flow → Save. Reopen through the Flows list. Use More options
for versions and replacement. Use the preview outcome menu for failures.
Reset preview clears only this prototype's browser storage.

Verification: `python app/static/recording-preview/verify_preview.py` passed.
Browser coverage includes the eight-step journey, missing completion check focus,
save failure, incorrect report, missing download, disconnection, sign-in,
cancellation, step editing/undo, saved versions, replacement, both methods,
pending setup preservation and schedule-only changes. Overflow checked at
1440×1000, 1280×800 and 390×844; screenshots visually reviewed.

All recording and execution behavior is simulated; Finish recording loads eight
fictional actions. Real recorder, API/database, downloads, CI, deployment and
original work-PC flow verification: **NOT RUN**.

The owner reviewed the minimal revision below and approved implementation.

## Minimal review revision

The review now contains one step list with inline editing, Test recording as
its primary action, and Save draft as its secondary action. The permanent
checks form and instruction rail were removed. The normal fictional recording
includes report and completion evidence; the “Missing ready check” scenario
asks one contextual question only when Test recording is selected.

The updated verify_preview.py passed: normal test/save journey, inline edit and
undo, missing-check prompt, defer/retry, and 1280×900 / 390×844 layouts.
This remains a simulated preview. For implementation evidence see the
[release report](../../../docs/testing/releases/2026-09-06-intuitive-recording/test-report.md).
