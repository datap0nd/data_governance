# Save recorded Flow without testing: test plan

- Change/PR: Allow a saved recording draft to become the Flow's runnable revision after one explicit confirmation; testing remains optional.
- Code baseline: `33619375f4b9db6eb2058f12e4e2b461ba0c0227` (`origin/main` before this change); final tested head is recorded in the report and PR.
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11/Python 3.13/Node 24, Chromium fixture, and the ASAP/GSCM work PC for later live verification.

## Prerequisites and test data

Use a disposable recorded Flow with fictional report/output data. Record at least one complete download action and return to Edit Flow without choosing **Test recording**. Keep a second incomplete recording without a download for the negative case. Do not publish private portal URLs, credentials, workbooks, cookies, or traces.

For the live case, update the deployed app and worker from GitHub `main`, confirm the work-PC capture feed is available, and use a harmless ASAP or GSCM report whose output can be independently checked. Disable any temporary schedule after the test.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| UI-01 | Open a complete untested recording, choose **Back to Edit Flow**, then **Save**. | A confirmation titled **Save without testing?** warns that the Flow may fail or produce wrong output and directs the owner to check the first run. | Synthetic browser assertion and approved clickable preview. |
| UI-02 | At the UI-01 confirmation choose Cancel/Go back. | No API save occurs; pending Flow settings and the recording draft remain available; Save is enabled for retry. | Synthetic browser assertion and preview walkthrough. |
| UI-03 | Repeat UI-01 and confirm **Save without testing**. | The Flow saves, reports **Flow saved without testing; check the first run output**, and returns predictably to Flows. | Synthetic browser assertion. |
| API-01 | PUT an untested draft as `recording_revision_id` without `allow_untested_recording`. | Save is rejected; Flow settings, active pointer, and draft status remain unchanged. | Python API/database test. |
| API-02 | Repeat API-01 with `allow_untested_recording=true`. | Revision is marked `approved`, its current configuration/transformation/core hashes are frozen, the Flow points to it, and `_build_job` produces a runnable recorded job. | Python API/database test. |
| API-03 | Try API-02 with a structurally incomplete recording lacking a download. | Save returns 422 and the complete transaction rolls back. | Python API/database test. |
| REG-01 | Test an already approved active revision. | Validation uses a new revision, so failure cannot invalidate the active approved revision. | Python API/database test. |
| REG-02 | Run all Python tests, every Node test file, and frontend syntax checks. | No regressions. | Local command output and final GitHub CI. |
| LIVE-01 | On deployed `main`, record one harmless ASAP Flow and one GSCM Flow; skip **Test recording**, save with confirmation, run once, and compare expected file names/schema/period and representative values. | Each Flow runs with the approved revision. The warning is visible before save and first-run output is manually verified. | Protected run IDs and opaque screenshot/log references only. |
| LIVE-02 | Cancel the warning on each portal, navigate away/back, and retry save. | No save occurs on cancel and all pending work is preserved for retry. | Protected evidence reference. |

## Automated checks

Run from the repository root with `PYTHONPATH=.`:

```powershell
python -m pytest tests -q
Get-ChildItem tests/test_*.mjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
node --check app/static/app.js
node --check app/static/users.js
node --check app/static/flow_run_log.js
node --check app/static/flow_recordings.js
node --check app/static/flow_recording_editor.js
node --check app/static/flow_recording_model.js
```

On the local Windows ARM environment only, pytest's dead-symlink cleanup hook may need to be disabled because Windows reports error 1463 after tests finish. This does not skip tests; record that infrastructure workaround separately from test results. GitHub CI runs the canonical command unchanged on Windows and Ubuntu.

## Acceptance and cleanup

Accept when confirm/cancel behavior preserves user intent, the backend requires explicit opt-in, approved revisions build runnable jobs, incomplete recordings roll back, the final full suite and CI pass, and the PR is merged to `main`. Live portal checks remain explicitly BLOCKED or NOT RUN until performed on deployed `main`.

Delete fictional preview/test Flow artifacts, disable temporary schedules, and retain only sanitized evidence. Roll back by reverting the merge; existing validated recordings remain compatible because `validated` status behavior is unchanged.
