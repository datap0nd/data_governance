# Portal recordings and global browser settings

For step-by-step work-PC verification, use the [Flows test plan](testing/releases/2026-09-05-flows/test-plan.md)
and [test report](testing/releases/2026-09-05-flows/test-report.md).

Flows → **Settings**, immediately after Run history, selects Google Chrome or
Microsoft Edge for every newly queued portal run, scan and recording. Chrome
is the default. Both use the same Playwright implementation and saved Flow
definition. Already queued/running jobs retain their browser. Install the
selected browser on the worker PC; a launch failure reports the browser name
and never silently switches to another browser. File, Outlook and SQL-only
jobs do not need to launch a browser.

Chrome and Edge use separate worker profiles. First use of Chrome may require
sign-in in each configured worker slot. Existing Edge profiles are retained.
The supported deployment version is Playwright 1.62.0. Browser installation,
enterprise policies, SSO/MFA and live report behavior must be checked on the
work PC before comparing success rates.

## Authoring

Choose **Create flow → Record a portal flow**, select ASAP or GSCM and optionally
supply a known report URL. A successful catalog scan is not required. In Edit
Flow, **Record my actions** is the default for new ASAP/GSCM flows; **Use detected
controls** remains available. Existing flows retain their method.

1. Open **Review recording** from Edit Flow. It opens a full-page view and keeps
   your pending setup intact. **Back to Edit Flow** returns to those values.
2. **Start recording** opens the existing Playwright recorder on one capable
   visible worker. Complete sign-in, open the report, run it and download the
   required files. **Finish recording** imports the actions. Playwright's red
   square only pauses capture.
3. Review the short step list. Click a step to edit it inline. **Step name
   (display only)** changes its Metronome label; **Target name (used during
   playback)** retargets a semantic portal element, such as changing a copied
   button from Country to Main, without changing its selector structure. Raw
   selectors expose their repair controls beside the target. Undo and movement
   controls stay with the selected step; frame diagnostics remain under Advanced.
4. **Save draft** preserves incomplete recordings. **Test recording** is optional:
   it asks only for missing essential report/ready information, then saves and tests
   the exact recording with the pending Edit Flow settings. Progress and errors
   appear beside the actions. Polling preserves edits and selection.
5. Choose **Back to Edit Flow → Save**. If the selected recording was not tested,
   confirm **Save without testing**. Settings and that revision apply atomically and
   the Flow can run; check the first run output because no test evidence exists.
   Cancelling the confirmation preserves the form and recording draft. A tested
   save still applies normally, and testing alone neither activates the recording
   nor enables scheduling.
6. **Record again** in the main action row immediately starts a replacement;
   unsaved editor-only changes are not saved first. **More → Saved versions**
   opens preserved revisions. Failed tests and abandoned edits keep the active version.
   An identical draft save preserves existing evidence. Retesting a validated
   version uses a new revision so failure cannot invalidate its active evidence.

Use the [release test plan](testing/releases/2026-09-06-intuitive-recording/test-plan.md)
and [report](testing/releases/2026-09-06-intuitive-recording/test-report.md) for
verification and the explicit remaining live checks.

Validation runs the downloads and configured Python transformation in a private
folder. It does not publish production files or execute SQL. Trace evidence is
kept under `.recording-validation/<session>/` in the managed Flow folder.
Cancel discards unsaved actions and closes the recorder's owned process tree.
The reservation remains occupied until the worker acknowledges cancellation.
If it has not responded after ten seconds, **Force close recording** stops and
fences that exact worker. Cancellation during authentication/validation also
uses the exact-worker stop. Lease expiry and worker restart
invalidate unfinished sessions. The updater sees these reservations as active
catalog operations and waits for them to drain.

### Multiple files and explicit dates

For different outputs from one report, record all their downloads, wait for
completion, then Finish once. Metronome checks the complete output bundle.
It does not automatically stop at the first download.

Date batching is removed. Older batched flows are paused; queued batched jobs
are cancelled with a review reason. Historical revisions, artifacts and copied
portable scripts remain historical evidence. Use **Convert to one range** with
explicit start/end values to create a new draft, then test it before enabling.
Conversion never turns the old whole batch into an automatic large export.

Definition version 3 adds semantic week ranges. In **Review recording**, select
one recorded element step, open **Advanced**, and enable **This is a range
step**. A single recorded week-cell click is sufficient; no consecutive-click
pattern is required. Confirm the fixed ISO start week and containing element box. The
saved action identifies that container rather than screen coordinates or a
fixed list of weeks. Playback re-reads eligible cells after every scroll or
calendar-page repaint, selects only unselected cells from the fixed start
through the newest enabled week, and verifies a complete, duplicate-free range
before allowing the download. Disabled future weeks are ignored. Ambiguous
dates, missing weeks, unexpected selections, unreadable selected state, or
stalled navigation fail before download.

Version 2 continues to support readable labels and cancellable Wait actions of
1-600 whole seconds. Workers advertise `recorded_flows_v3` before claiming new
recording or validation jobs. Existing non-batched version 1 and version 2
definitions remain readable and unchanged. New recordings use version 3; an
existing draft is upgraded when a range step is saved.

Recorded outputs keep `output.format: "xlsx"` as the semantic Excel-family
choice. Metronome recognizes `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, `.xlt`, `.xltx`
and `.xltm`, preserves the compatible extension supplied by the browser, and
normalizes worksheet values to CSV only when downstream processing or data
checks require it. Legacy OLE/BIFF files and OOXML/XLSB ZIP containers are
validated from their contents, including ZIP packages with leading transport
bytes. On the Windows BI desktop, a modern `.xlsx`/`.xlsb` download wrapped in
an NASCA OLE encryption container is opened read-only through desktop Excel and
read through pywin32 in bounded cell batches into the same normalized CSV/SQL
path. The original browser path is opened directly; Excel does not save another
workbook or CSV. Excel links, alerts and workbook macros are disabled for that
operation, while the desktop's existing integration-event setting is preserved.
Other corrupt or encrypted
workbooks, executable Excel add-ins, arbitrary binary files, and sign-in pages
fail with format-specific diagnostics.

Recordings are parsed as Python syntax, never executed as imported code. Only
the supported action/locator model can activate. Coordinate/forced actions,
arbitrary code, positional locators, unknown pages and unsupported methods are
rejected. The editor can repair locators using exact text, labels or stable CSS
while retaining the frame chain. GSCM recycled virtual row/cell IDs require
repair; bookmark identity selection remains preferable for bookmark lists.

### GSCM Favorite bookmark clicks

The recorder captures a click on whatever row the Favorite grid had rendered;
it cannot know that the grid virtualizes and recycles rows. Import therefore
inspects the recorded component path: a click inside `div_favorite`/
`grd_bookmark` is suggested as **Bookmark in GSCM Favorite list**. When the
recorder captured the row's exact text the target is filled in automatically;
when it captured only a recycled `gridrow_N` ID the step asks for the exact
bookmark name beside it. The rest of the journey (Setting, scope tab, folder
clicks and the following Go) stays recorded and unchanged.

Playback resolves the exact name (plus optional scope and stable bookmark ID)
in the grid's bound `gds_bookmark` data, then clicks the freshly rendered row
if it is in view. Otherwise it walks the grid's own scrollbar buttons to the
confirmed top and sweeps downward one overlapping page at a time, waiting for
an observed rendered-row change after each movement and reacquiring rows after
every repaint. Same-named bookmarks are accepted only when the visible folder
rows confirm the intended folder; duplicates inside one folder fail closed.
The step stops on the target, a confirmed end, a stalled scrollbar, run
cancellation or a 120-second deadline, and reports a collapsed ancestor as an
actionable failure. It never replays mouse wheel gestures, forces clicks,
reuses row handles or searches the whole page. Native Nexacro selection stays
disabled until the work-PC investigation qualifies an exact sequence. Run logs
record the strategy, movement/rendering counters and the stop reason.

Navigation completion is appropriate for a report produced by the document
response. For a page that calculates asynchronously, select its Run/Generate
action and a loading cycle or changed result value. An HTTP 200 response alone
does not prove a cached or asynchronously calculated report is current. Specify
schema and period checks wherever those values are available in the output.

## Authentication and browser isolation

Recorded execution uses a fresh browser context, initialized from the selected
browser's authenticated state. The initial state can be seeded from the existing
automation profile, and existing authentication helpers still recover expired
sessions. On Windows, stored state uses the current user's DPAPI protection.
On Unix, the state file is created with user-only permissions. Browser state is
never embedded in the generated Python script or the Flow database.

The recorder uses the public `codegen --load-storage --save-storage` interface.
Its temporary plaintext handoff file lives in a restricted, worker-owned
directory and is removed when the recorder finishes. Worker/profile ownership
is retained throughout. The recording browser does not reuse its download
history across sessions.

This is an evidence-driven adjustment to the original persistent-profile
recording plan: on the development Windows ARM machine, both installed Chrome
and Edge crashed on repeated persistent-profile downloads in a minimal
Playwright reproduction outside Metronome. Fresh recorded contexts avoid that
failure in the repeat-run fixture. Legacy catalog automation retains its existing
profile model; repeated launches of those flows still require the work-PC pilot.
Authentication that depends on extensions, client-specific profile state or
sessionStorage needs live qualification; successful cookie/localStorage/
IndexedDB handoff is not a claim that every corporate SSO method is equivalent.

## Portable Python

Activation creates `Scripts/run_flow.py` and an immutable, content-addressed
revision in `Scripts/versions/`. The file includes the Flow configuration,
recorded steps, parameters, output validation, normalization, Python
transformation, SQL handoff and referenced execution helpers as readable source.
There is no Metronome installation, database or adjacent execution configuration
requirement. Python 3.11+, the declared libraries, the saved browser, network
access and the executing account's credentials are required.

```powershell
python run_flow.py --dry-run
python run_flow.py --headed
python run_flow.py --headless --parameter start=2026-01-01
python run_flow.py --output-root D:\PortableFlows --profile-dir D:\PrivateFlowProfile
python run_flow.py --no-transform --no-sql
```

Use the same `--output-root` on later runs of a relocated copy. Only that Flow's
owned output folder can be reused. The root and profile are runtime locations,
not additional configuration files. Without overrides, the saved managed folder
and all configured stages are used. `--dry-run` does not sign in, query reports
or write SQL. Python transformations retain the existing `--input`/`--output`
subprocess contract. Local imports, adjacent file literals and dynamic code
dependencies must be made portable before validation.

The generated file records its generator, dependency versions, the execution
core it embeds, the core the active revision was last tested with, and the Flow
revision. Its content hash and current status are in the folder manifest. Saved
Flow files update automatically after changes, including global browser
selection and application updates; see
[team handover instructions](flow_standalone.md). An edited `run_flow.py` is
archived under `Scripts/versions` and refreshed on the next save; editing a
standalone file does not change scheduled execution. Workers and generated
scripts always use the current reviewed execution helpers. The core a revision
was tested with is kept as evidence only: after an application update, Flows
keep running on the updated code and their scripts are regenerated with it.

Flow, output, profile and SQL locks apply. A lost SQL outcome blocks retry until
reconciled. Standalone runs leave `Scripts/standalone-logs/sql-outcome.json` when
the SQL outcome is unknown. Confirm the database outcome before removing that
marker. `--no-sql` never clears an unresolved SQL marker. Resume replays session
steps with the original resolved dates; changed portal defaults require a new
run. SQL retry requires the complete validated/transformed output bundle.

## GSCM discovery

Normal discovery activates configured scopes and reads the source Nexacro
dataset using unfiltered APIs when available. It does not sweep rendered rows.
Selection finds the exact bookmark ID/name in the grid's current dataset, so
inventory indexes are never reused after sorting or filtering.

Global Flows Settings contains each GSCM portal's discovery coverage: module
codes, optional exact Favorite Combo component suffix, Private/Public/Custom
scope tabs and an explicit diagnostic rendered-grid mode. Missing/failed module
or scope activation, an unavailable dataset, filtered data without unfiltered
APIs, or an unobserved load produces incomplete coverage. A previous dataset
load cannot certify a newly requested scope. Builds that cache scope data without
emitting a qualifying load event need diagnostic verification before that
optimization can be accepted. Existing report-completion compatibility waits
remain where a stronger live signal has not been qualified.

Incomplete scans merge verified identities without retiring unseen bookmarks.
Restricted module/scope scans also preserve other catalog areas, and expose
`coverage_complete` separately from whole-catalog completeness. An empty catalog
never silently erases the previous GSCM snapshot.

## Live acceptance still required

Synthetic tests cover 350 loaded/filtered bookmarks, delayed/empty/failed loads,
sort changes and rejected selections; recording syntax and dates; replaced
iframes, popup exports and multiple downloads; schema/period/default failures;
capacity, cancellation, worker loss, SQL reconciliation and standalone repeated
download/transformation on Chrome and Edge. They are not a live portal pilot.

Before rollout, record the deployed commit/browser/Playwright versions and
profile routes. Pilot the previously failing GSCM bookmark IDs, first/middle/last
bookmarks, a MicroStrategy wizard and a custom HTML report. Run each recording
three times from fresh browser contexts, varying parameters when applicable.
Compare report identity, rows, periods and structure; workbook bytes can differ
because of metadata. Exercise expired SSO and a cancelled worker reservation.

Measure first-attempt success, recovered success, authentication interruptions
and latency separately. A short pilot cannot establish 99% first-attempt
reliability. No live deployment, bookmark inventory, MicroStrategy API privileges
or browser-versus-request equivalence has been established by local fixtures.
Recorded execution disables legacy HTTP export replay. Request/API acceleration
remains disabled until a per-report fresh-session comparison proves current
report generation, transient-token handling and output equivalence. Existing
file, Outlook and catalog Flow behavior remains on its existing execution path.

References: [Playwright authentication](https://playwright.dev/python/docs/auth),
[public recorder](https://playwright.dev/python/docs/codegen),
[downloads](https://playwright.dev/python/docs/downloads).
