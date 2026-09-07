# Reusable recordings and downstream view refresh: test plan

- Change/PR: **Choose from template** in the recording editor copies another
  Flow's saved recording (same portal module, ASAP or GSCM) into the current
  Flow as an independent draft; **Replicate flow** includes the recording by
  default. **Refresh materialized views** is an optional step directly below
  SQL insertion (Off, Automatic, Manual) with a shared executor for recorded,
  catalog, file and Outlook Flows, per-view persistence, fail-stop behavior,
  **Retry view refresh** recovery, Pipeline coordination, a worker capability
  and generated-script support (`--dry-run` list, `--no-sql`, checkpoints,
  `--retry-views`). Files: `app/flow_view_refresh.py` (new executor),
  `app/flow_view_refresh_discovery.py` (new), `app/routers/flows.py`,
  `app/routers/flow_recordings.py`, `app/routers/pipelines.py`,
  `app/flow_worker.py`, `app/flow_recording_runtime.py`, `app/flow_standalone.py`,
  `app/flow_activity.py`, `app/flow_parallel.py`, `app/database.py`,
  `app/static/app.js`, `app/static/flow_recording_editor.js`,
  `app/static/flow_run_log.js`, `app/static/style.css`,
  `app/static/recording-preview/templates-refresh*.{html,js}`, docs and tests.
  PR link: recorded in the test report's Merge evidence section.
- Code baseline: `e0353e0` (main after PR #84).
- Related report: [test-report.md](test-report.md)
- Intended environments: Metronome app plus Flow workers on the work PC,
  PostgreSQL with the `DG_UPLOAD_*` write role and at least one materialized
  view chain; the automated checks run anywhere with Python 3.11+, Node 22 and
  Playwright Chromium (CI installs Chrome/Edge).

## Prerequisites and test data

- Update the app and every worker from the merged `main` revision. Workers
  register `post_sql_refresh_v1`; an older worker never receives a run whose
  frozen plan refreshes views, so update workers first when enabling the step.
- Migrations add `flows.post_sql_refresh_json`, `flow_runs.sql_outcome_json`,
  `flow_run_view_refreshes`, `flow_recording_revisions.template_source_json`
  and replace the recorded-run reconciliation trigger with
  `recorded_flow_unknown_sql_commit_v2` (a confirmed commit no longer flags
  reconciliation when a later view fails). They run on startup.
- Fictional preview: serve `app` with
  `python -m http.server 8769 --bind 127.0.0.1 --directory app` and open
  `http://127.0.0.1:8769/static/recording-preview/templates-refresh.html`.
  Production `app.js`, the recording editor and the run-log page render every
  screen with fictional data; the preview bar switches the discovery result
  and the retry outcome.
- Live data: two ASAP or two GSCM Flows where one has a saved recording; a
  Flow whose SQL table feeds a chain `table → view → mv_a → mv_b`; run the
  **PostgreSQL lineage** scan first so the dependency metadata is current.
  Use disabled test Flows and a safe SQL target.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| TPL-01 | Open a GSCM Flow's recording editor with no recording; choose **Choose from template**. | A searchable list of other GSCM Flows with saved recordings shows name, website, step count and status (Active/Tested/Draft); ASAP Flows and Flows without recordings are absent. Search narrows the list; an empty search result and a load error are explained beside the list. | Preview screenshot `recording-template-list.png` |
| TPL-02 | Select a Flow; change the **Version** select; inspect the step preview. | The active recording (or latest draft when none is active) is preselected as default; older versions are selectable and the step list, download badges, parameter and bookmark-target summary update. | `recording-template-preview.png` |
| TPL-03 | Choose **Cancel**, then reopen and choose **Use this recording**. | Cancel changes nothing. Applying replaces the editor with an independent draft: the state reads "N steps recorded", the save state says "Copied from …", the destination's Saved versions gain a draft labelled "from <source>", evidence and activation are not copied and **Test recording** is required before activation. | `recording-template-applied.png`, API test `test_recording_templates.py` |
| TPL-04 | On a Flow that already has steps, apply a template, then **Undo**, then **Save draft**, then open **Saved versions**. | Unsaved edits are saved as their own version before the copy; Undo restores the previous steps (marked unsaved) and saving keeps them; Saved versions list both the template copy and the restored draft. | `recording-template-undo-history.png` |
| TPL-05 | In Edit Flow change the website to ASAP without saving, open the recording editor and the picker. | Only ASAP recordings are listed (server enforces the module with the pending website); copying a GSCM recording into an ASAP Flow is rejected with a clear message. | API test, preview walkthrough |
| TPL-06 | New flow → **Replicate flow**: choose a recorded Flow, keep **Include its recording** checked, **Copy settings**, name the flow, **Create flow**. | The new flow is created paused with its own settings, the recording is copied as a new draft revision (not the source revision ID), and the recording editor opens on the copy. Unchecking the box creates the flow without a recording. | `replicate-new-flow.png`, `replicate-copied-recording.png` |
| TPL-07 | After copying, edit or delete the source recording. | The copy is unchanged. | API test |
| VR-01 | Edit a Flow with SQL insertion enabled; open **After download**. | **Refresh materialized views** appears directly below the SQL target with Off (default), Automatic and Manual. Existing and new Flows read Off until changed; an older client that omits the field keeps the saved setting. | `builder-automatic.png`, API test |
| VR-02 | Choose Automatic; **Preview discovered views**. | The ordered downstream list (upstream first, ordinary views traversed but only materialized views listed, duplicates removed) and the metadata timestamp appear. A verified empty list reads "Verified: no materialized view reads this table". | `builder-automatic.png` |
| VR-03 | With the table missing from the metadata, incomplete lineage, stale metadata (>48 h) or a dependency cycle. | The blocker is explained beside the control with **Refresh metadata** and/or **Use Manual** actions; stale metadata is a warning with the timestamp. Queueing a run in a blocked state is rejected with the same explanation. | `builder-blocked-missing.png`, API test |
| VR-04 | Change the SQL database, schema or table. | The automatic preview is invalidated and asks for a new preview. | Preview test |
| VR-05 | Choose Manual; search the catalog, add views out of order, **Add view manually** with a non-existent view, **Verify selection**. | Catalog matches are listed; verification reports existence, materialized-view type and refresh permission problems per view; after removing the bad view the selection is verified, ordered upstream first and saved in that order. | `builder-manual-verify-failed.png`, `builder-manual-verified.png` |
| VR-06 | Run a Flow with views configured; open the Flows page and the run log. | Progress shows **SQL insertion → Refresh materialized views** with the current view and completed count. The run log lists each view's status, duration and error separately from the SQL outcome. Test recording performs neither SQL insertion nor refresh. | `run-log-failed-view.png`, live check |
| VR-07 | Make one view fail (for example lock it in another session) and run. | The run fails after the SQL insertion committed; earlier views stay refreshed, later ones are skipped; the message says SQL already committed; `sql_reconciliation_required` is not set; **Retry view refresh** appears in run history and the run log while **Retry SQL only** does not. | `runs-retry-available.png`, `run-log-failed-view.png` |
| VR-08 | **Retry view refresh**. | A `view_retry` run refreshes only the unfinished views (no download, transformation or insertion), keeps completed checkpoints, and ends succeeded; when it fails again recovery stays available from the newest run. Retry is refused while the run is active, when SQL did not commit, when a newer run already committed SQL, or when every view finished. | `run-log-retry-succeeded.png`, `runs-retry-failed-again.png`, API test |
| VR-09 | Preview and start a full pipeline whose Flows configure views; queue a direct run of such a Flow while the pipeline holds the lock. | The pipeline plan lists each configured view once, upstream first, in its refresh stage; child Flow runs defer their views to the parent; the direct run is rejected while the view is reserved and a pipeline preview is blocked while a Flow run refreshes the view. | API test, live check |
| VR-10 | Generate the Flow's script; run `--dry-run`, `--dry-run --no-sql`, a normal run with a failing view, then `--retry-views`. | Dry run prints `refresh_views`, `refresh_mode` and `refresh_frozen_at`; `--no-sql` skips both stages; a failed view leaves `Scripts/standalone-logs/view-refresh-checkpoint.json`; a plain rerun is refused until `--retry-views` finishes the remaining views; a blocked frozen plan refuses to run. | API test, live check |
| VR-11 | Old worker: register a worker without `post_sql_refresh_v1` while a run with views is queued. | The worker does not claim the run; an updated worker does. | API test |
| UX-01 | Repeat TPL-01…04, VR-01…05 and the run log at 390 px width. | No horizontal overflow; controls, feedback and next actions remain visible. | `builder-narrow.png`, `recording-template-narrow.png`, `run-log-narrow.png` |

TPL-01 to TPL-06, VR-01 to VR-05, VR-07, VR-08 and UX-01 run locally against
the fictional preview and the API tests; VR-06, VR-09 (execution) and VR-10
(execution) need the work PC, PostgreSQL and workers.

## Automated checks

```bash
python -m pytest tests/test_view_refresh.py tests/test_recording_templates.py tests/test_templates_refresh_preview.py -q
python -m pytest tests/test_flow_recordings.py tests/test_recording_visual_editor.py tests/test_optional_recording_editor.py tests/test_recording_playback_ui.py tests/test_managed_flow_editor.py tests/test_flow_standalone.py tests/test_flow_handover.py tests/test_flow_activity.py tests/test_flow_parallel.py tests/test_pipelines.py tests/test_flows.py -q
python -m pytest tests -q
node --check app/static/app.js && node --check app/static/flow_run_log.js && node --check app/static/flow_recording_editor.js
node tests/test_flow_builder_contract.mjs && node tests/test_flows_display.mjs && node tests/test_recording_visual_model.mjs
PREVIEW_EVIDENCE_DIR=docs/testing/releases/2026-09-07-reusable-recordings-view-refresh/evidence python -m pytest tests/test_templates_refresh_preview.py -q
```

## Acceptance and cleanup

Accepted when the automated checks pass on the final head, the preview
walkthrough shows every changed control including failure and recovery, and
the live checks VR-06, VR-09 and VR-10 either pass on the work PC or remain
explicitly NOT RUN in the report. Cleanup: set test Flows back to Off, delete
test drafts copied from templates, remove `view-refresh-checkpoint.json` from
test script folders. Rollback: the new columns and table are additive; setting
every Flow to Off restores the previous run behavior and the previous trigger
can be recreated from the migration history.
