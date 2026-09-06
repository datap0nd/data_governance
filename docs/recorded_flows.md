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
3. Review the short step list. Click a step to edit it inline. Undo and movement
   controls stay with the selected step; selectors and frame diagnostics remain
   under Advanced. Date and download options use the existing execution engine.
4. **Save draft** preserves incomplete recordings. **Test recording** asks only
   for missing essential report/ready information, then saves and tests the exact
   recording with the pending Edit Flow settings. Progress and errors appear
   beside the actions. Polling preserves edits and selection.
5. After success, choose **Back to Edit Flow → Save**. Settings and the selected
   tested revision apply atomically. Testing alone neither activates the recording
   nor enables scheduling. Output or execution edits require retesting;
   scheduling-only changes do not.
6. **More → Saved versions** opens preserved revisions; **Record again** creates
   a replacement draft. Failed tests and abandoned edits keep the active version.
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

Definition version 2 supports readable labels and Wait actions of 1-600 whole
seconds. Waits send progress/heartbeats and remain cancellable. They supplement
report completion checks. Workers advertise `recorded_flows_v2`; older workers
cannot claim new recording or validation jobs. Existing non-batched version 1
definitions remain readable. Edited definitions are saved as version 2.

Recordings are parsed as Python syntax, never executed as imported code. Only
the supported action/locator model can activate. Coordinate/forced actions,
arbitrary code, positional locators, unknown pages and unsupported methods are
rejected. The editor can repair locators using exact text, labels or stable CSS
while retaining the frame chain. GSCM recycled virtual row/cell IDs require
repair; bookmark identity selection remains preferable for bookmark lists.

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

The generated file records its generator, dependency versions, definition/core
hashes and Flow revision. Its content hash and current status are in the folder
manifest. Regenerate after changing global browser selection when exporting a
new standalone copy. Modified launchers and immutable versions are preserved;
editing a standalone file does not change scheduled execution. Workers use the
same reviewed execution helpers and reject jobs pinned to a different core.
After an execution-core change, save and validate a new recording revision.

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
