# Recording journey previews

The current preview is [optional.html](optional.html): recording tests start
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
