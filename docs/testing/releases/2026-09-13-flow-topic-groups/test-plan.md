# Flow topic groups: test plan

- Scope: persistent Flow topic groups and atomic manual group runs, following the owner-approved fictional preview.
- Change: [PR #122](https://github.com/datap0nd/data_governance/pull/122).
- Base: `origin/main` at `84a8ea9` (PR #121).
- Preview: `app/static/recording-preview/groups.html`.
- Related results: [test-report.md](test-report.md).
- Environments: isolated static HTTP server, Windows, checkout-owned Python 3.13 with the locked dependencies, synthetic Chrome browser data. No application server is needed for this preview.

## Approved behavior

One level of named groups inside each source module and classification. A group
row aligns with standalone flows and expands to reveal its members. Each module
has a **Group** button; creation asks only for a name and at least two flows.
The checklist can search hundreds of flows without losing selections. A flow
belongs to one group; selecting an already grouped flow moves it into the new
group. **More > Edit group** changes the name or members; **Ungroup** keeps every
flow. Existing individual configuration, schedules and Run actions remain.

**Run group** queues all members together for the existing worker pool. Available
workers determine actual concurrency. If a member cannot be queued, the group
operation queues none and explains the blocker. An active member prevents a
duplicate group run; other idle members can still be run individually. Active
flows use the existing execution pane and return to their group after finishing.

The organizational pattern draws on [Make's folders and labels](https://help.make.com/organize-scenarios-with-subfolders-and-labels).
The proposed Metronome journey uses a single group level without additional
group execution configuration.

## Prerequisites and test data

Run from this checkout:

```powershell
.\tools\check.ps1 -Mode Setup
```

Chrome must be installed for the synthetic browser selector `channel='chrome'`.
The fixture starts a static server and closes it automatically. It contains
fictional M Tracker Country, M Tracker Subs, Photo, Wi-Fi and TI flows in ASAP,
plus one sample in each of GSCM, Outlook and Local. The two M Tracker flows use
distinct fictional SQL table names. Preview actions are held in tab memory.

For an interactive review, serve `app/` as the web root:

```powershell
.\.venv\Scripts\python.exe -m http.server 8767 --bind 127.0.0.1 --directory app
```

Open [the local preview](http://127.0.0.1:8767/static/recording-preview/groups.html).
The dashed preview toolbar supplies reset, failure simulation and a 200-flow
fixture. Those controls belong only to the preview.

## Test cases

| ID | Exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| G-01 | Open the preview. Compare M Tracker with Photo. Expand M Tracker, collapse ASAP, then reopen ASAP. | Names align; Country and Subs appear only when expanded; the topic remains expanded after toggling its source module. | Collapsed and expanded screenshots; browser assertions. |
| G-02 | Expand M Tracker; click Country's Edit, return, then run Country alone. Stop it. Run the group. Use **Try recovery & scale > Finish active runs**. Run Photo alone. | Individual table settings remain distinct. One individual run appears first; group Run is disabled while a member is active. The group starts two fictional runs; completed members return to the topic. Standalone Run remains available. | Running screenshot and observed row counts. |
| G-03 | Enable **Next save fails**. Click ASAP's **Group**. Submit blank; enter Connectivity and submit with no selections. Select Wi-Fi and TI. Search for an absent name, then clear search and save. Retry the failed save. | Required-name and minimum-selection errors appear beside Save. Empty search explains the next action. Selections survive filtering and the simulated network error; retry creates the group once. | Save-recovery screenshot and checkbox/name assertions. |
| G-04 | Edit Connectivity. Try naming it M Tracker; then rename to Weekly topics and add Photo. Edit again, change its name and Cancel. Reopen, add Country, save, then Ungroup. Open GSCM's Group editor and close it with ×. | Duplicate group name rejected. Cancel preserves the saved group. More closes when entering the editor. Moving a flow removes its old membership; ungrouping preserves all flows and SQL configuration. GSCM checklist only contains GSCM flows. | Browser assertions and recovery report. |
| G-05 | Enable **Next run is blocked**; Run group. Then enable **One worker available** and retry. | No partial group run on validation failure; error names the affected member and recovery action. Retry queues both; one runs and one visibly waits. | Blocked-run screenshot and execution rows. |
| G-06 | Finish active runs; **Use 200 flows**. Group ASAP flows named Regional report 199 and 200 by changing search between selections. At 390×844 save the new group, then Reset preview. | Search narrows 197 ASAP flows; both selections survive; the dialog and page fit the viewport while the table scrolls horizontally. Save and Reset work. | Narrow search screenshot and viewport assertions. |

## Automated checks

The smallest non-overlapping preview set, plus JavaScript and Python syntax:

```powershell
$env:PREVIEW_EVIDENCE_DIR = Join-Path (Get-Location).Path 'docs/testing/releases/2026-09-13-flow-topic-groups/evidence'
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_topic_groups_preview.py -SyntaxPath app/static/recording-preview/groups.js,tests/test_flow_topic_groups_preview.py
```

After a failure, run only the failing test and necessary companions, as recorded
in the report. No local full suite is part of this preview review.

The implementation check uses a temporary SQLite database, the real HTTP
handlers and UI, a static fixture shell and simulated worker launchers:

```powershell
$env:GROUP_EVIDENCE_DIR = Join-Path (Get-Location).Path 'docs/testing/releases/2026-09-13-flow-topic-groups/evidence'
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_topic_groups.py,tests/test_flow_topic_groups_browser.py,tests/test_auto_update.py::test_update_drain_middleware_blocks_new_work_but_not_worker_progress -SyntaxPath app/database.py,app/main.py,app/routers/flows.py,app/routers/flow_groups.py,app/static/app.js,app/static/flow_groups.js,tests/test_flow_topic_groups.py,tests/test_flow_topic_groups_browser.py,tests/fixtures/flow-groups/harness.js
```

| ID | Implementation procedure | Expected result |
| --- | --- | --- |
| I-01 | Create, rename, replace all members, restart database initialization and ungroup. Compare every original `flows` column before/after. | Membership persists; configuration, schedule and paths are identical; group identity survives replacing every member. |
| I-02 | Move all members into another group; submit a stale edit/deletion; submit blank, duplicate, missing, wrong-module/classification and invalid-ID payloads. | One group per flow; empty groups disappear; stale/invalid writes reject without changing saved membership. |
| I-03 | Move a member to Draft and delete the last remaining synthetic member. | Moved flow becomes ungrouped in Draft; deletion cleans the now-empty group. |
| I-04 | Queue two flows. Inspect queue contents at launcher invocation, then let two simulated workers claim. | Both durable jobs exist before launch; timestamps match; each keeps its independent settings; both workers can claim separate members. |
| I-05 | Block the second member's job or configure conflicting shared output; retry after resolving. | Entire batch rolls back on validation/reservation failure; worker launch is not attempted before commit. Recovery queues both. |
| I-06 | Submit the same batch concurrently; run with stale member IDs; retry an existing queued member individually. | Exactly one batch queues; unseen members are not run; individual Start now reuses its run ID. |
| I-07 | Make the launcher return an error or raise; restore it and use individual Start now. Queue a separate Local group. | Failed startup leaves durable queued runs with recovery feedback. Local members retain normal manual force-reprocess behavior and unchanged schedules. |
| I-08 | Walk real UI/API creation with a failed save, retry, reload, duplicate-name editing, moving members, narrow viewport and ungrouping. | Work is preserved on error; reload proves persistence; all flows survive ungrouping. |
| I-09 | Walk actual group Run, blocked response, retry, activity polling, finish runs while editing another group, then individual Run/Stop. | Feedback and execution rows update; unsaved name/selection remain; grouped members return to their correct rows. |
| I-10 | Run existing source-group, sort and polling Node contracts through the Python verifier bridge; exercise the update-drain middleware. | Existing list contracts pass; new group runs respect the same maintenance barrier as individual runs. |
| I-11 | In the existing fictional worksheet UI server, return an empty list from the new groups endpoint. Walk worksheet error recovery, saved choices and the Local/portal builders. | Ungrouped flows still open, edit, save and recover; worksheet selections and SQL targets remain unchanged. |

The CI diagnosis identified this additional affected browser fixture. Its
targeted regression command is:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_excel_ui.py::test_excel_failure_recovery_and_saved_choices,tests/test_flow_excel_ui.py::test_excel_setting_in_each_builder_defaults_off -SyntaxPath tests/test_flow_excel_ui.py
```

Final-head **Merge ready** CI supplies full regression before the implementation
PR can merge. Do not repeat the full suite locally.

## Usability evidence

The preview loads the existing `style.css`, bundled Outfit font, Flow table and
individual row renderers. Only topic controls and the in-memory simulation are
new. Browser checks capture desktop and narrow layouts, reject JavaScript errors
and confirm requests remain on the fixture's static localhost server.

Owner approved the preview on 2026-09-13: “Approve this design and continue to
implementation and merge.” The implementation retains the demonstrated journey.

## Acceptance and cleanup

Feature delivery requires focused local implementation evidence, the final-head
CI run linked in the PR, and a merge to `main`. Preview evidence alone does not
establish delivery.

Reset or close the preview tab to discard fictional changes. Stop only the
static server started for this preview after review. The integrated fixture
server and browser close automatically; managed files are under isolated test
roots. Retain sanitized release evidence. Upgrade using the normal app update
and restart: database initialization adds the two organizational tables without
rewriting flows. To undo grouping, use **Ungroup**; flows and their settings stay
intact. A code rollback can leave the additive group tables in place. It does
not need to undo flow configuration or schedules.
