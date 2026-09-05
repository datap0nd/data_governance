# Flows: work-PC testing instructions

This first package covers [recorded flows and browser settings (#67)](https://github.com/datap0nd/data_governance/pull/67),
[worker capacity (#68)](https://github.com/datap0nd/data_governance/pull/68) and
[recording controls and date batches (#69)](https://github.com/datap0nd/data_governance/pull/69).
The cumulative application baseline is main commit
`bfff7bd0aa3af4a21f02706db65bc3b3e0524694`.

Read the [actual test report](test-report.md) before interpreting this plan.
All work-PC cases below start **NOT RUN** in the [results worksheet](manual-results.csv).
The automated fixtures are already verified; the live portal checks are not.

## Prepare the work PC

1. Update Metronome from main using its normal update/setup path. Record the
   installed app commit and each participating worker's version. Confirm the
   update includes the baseline above. Do not mix old worker code with new
   recording controls. Save and validate a **new revision** of any recording
   created before the execution-core update in #69.
2. Record Windows, Python, Playwright, Chrome and Edge versions, the Flow's
   timezone and the worker's timezone. Playwright **1.62.0** is the tested
   deployment version. Use each browser's About screen to get its actual build.
   Follow [worker setup](../../../flow_workers.md) for missing services/tasks;
   adding headless services requires interactive credential enrollment.
3. Take note of current Flows → Settings: browser, total/background/visible
   capacities, per-portal limits and GSCM scan coverage. Existing saved limits
   are preserved on upgrade. A saved visible capacity of five is valid; clicking
   Record flow should still start just one recording worker.
4. Create disabled test flows in managed folders **outside the checkout**.
   Use names prefixed `TEST-`, dedicated destinations and test-only SQL tables.
   Leave SQL off until the SQL-specific cases. Keep production schedules and
   destinations out of failure-injection tests. Store screenshots, comparison
   workbooks and traces in protected storage, not this GitHub repository.
5. Prepare a small report for each relevant shape: GSCM bookmark, GSCM recorded
   report, ASAP MicroStrategy wizard, ASAP custom HTML report; include iframe,
   popup and multiple-download examples where available. Record a manual
   baseline export and its report identity, filters, schema, row counts and
   date bounds. Start date testing with small results before two-year exports.
6. For GSCM inventory, prepare the expected ID/name/module/scope list, including
   previously failing IDs, duplicates and first/middle/last entries in a list
   of at least 300. Distinguish loaded dataset rows from any server-side paging.

For each attempt, duplicate the relevant worksheet row and fill in timestamp,
tester, app/worker revisions, browser, run/session ID, actual result and evidence
reference. Do not replace earlier failed attempts. If the portal cannot supply
a case, mark it BLOCKED with the missing prerequisite rather than PASS.

## Recording controls — test these first

Use **Create flow → Record a portal flow**, or an existing draft's
**Record / review flow**. The expected boundary is: open the configured starting
page, navigate/filter, **complete every required download**, then click
**Finish recording in Metronome**. The Playwright red square pauses recording.

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| ENV-01 | Open Flows → Settings; verify the tab follows Run history. Note the deployed versions and saved capacities; choose Chrome and save. | Setting persists after refresh. Worker versions support recording controls; selected browser is installed. Record version/settings evidence. |
| REC-01 | With no active headed work, leave multiple visible slots configured. Create a draft with a known report URL and click Record flow once. Observe worker activity and all opened windows. | Exactly one slot is reserved for this recording. Only that slot authenticates. No pool-wide sign-in or command windows. The report browser and Playwright Inspector are expected; authentication may briefly precede them. Record session/worker ID and window count. |
| REC-02 | On a small report, navigate, set filters, download one file and wait for completion. Leave Chrome and Inspector open; click Finish recording in Metronome. | Status progresses through finishing to a reviewable draft. Both owned recorder windows close, steps include the download trigger, and the slot becomes available. No manual code copying. Compare expected step sequence. |
| REC-03 | Record two different required outputs from the same report. After the first file, continue to the second. Wait for both, then Finish once. | First download does not end recording. Review contains both outputs correlated with their actions. Validation later requires both files, with no overwrite or missing output. |
| REC-04 | Start a fresh draft, click Playwright's red square, observe Metronome, then resume recording. Download a small file. Close only the report browser; if the session remains active, Finish in Metronome. | Pause is not reported as completion. Closing Chrome alone may leave Inspector open. Finish ends the remaining owned session and imports saved actions, or a clear actionable failure is shown; it must not remain silently active. |
| REC-05 | Record navigation/filtering without a download, then Finish. Try saving/activating the partial result. | Actions remain in a draft with a missing-download warning. The incomplete recording cannot activate. Attach a real download to its observed trigger during review or re-record, then validate before activation. |
| REC-06 | Start recording; click Cancel while the recorder is open. Try repeated clicks during the request. | UI shows cancellation in progress and prevents duplicate submissions. Owned recorder/Inspector close, unsaved actions are discarded, reservation releases only after worker acknowledgement, and no late draft/success resurrects the cancelled session. |
| REC-07 | On a disposable worker/session, exercise cancellation while sign-in waits for user input, then during revision validation. For an unresponsive recorder use a controlled fixture or test worker; request Cancel and wait at least ten seconds. | Auth/validation stop targets the assigned worker. A recording that has not acknowledged Cancel offers Force close recording after the grace period; use it and verify only that worker is fenced/stopped. Other workers and unrelated browsers remain intact. Do not freeze a production worker to manufacture this case. |
| REC-08 | During a disposable recording, briefly block the Metronome status request in browser developer tools or interrupt only the test client's connection. Restore it; use Finish/Cancel after polling resumes. | Status polling recovers; buttons remain clickable and do not duplicate or disappear indefinitely. Record transient error and recovered terminal state. |
| REC-09 | With a known unrelated queued test run and a free headed slot, start a recording. Separately repeat while all eligible slots are already busy. | Pinned slot's next assignment is the recording, ahead of unrelated queued work. Active jobs are not preempted. Capacity exhaustion is visible; the action must not start the whole pool. |

## Review, report correctness and activation

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| REV-01 | Review REC-02: set exact Report title and correct page/frame, Report generation action and Completion signal. Configure expected output format/columns and period checks where available. Save revision → Validate → Activate; enable a test schedule in Pipeline and schedule settings. | Validation downloads/transforms in a private validation folder, without publishing production files or executing SQL. Only a successfully validated revision activates. A scheduled run records the revision, expected report and verified outputs. Compare against the manual baseline. |
| REV-02 | Repeat recording/review/validation for an ASAP MicroStrategy wizard and a custom HTML report, plus an iframe that is replaced and a popup export when available. | Each uses its actual recorded controls. Custom HTML does not inherit unrelated wizard options. Correct frame/page identity survives replacement/popups; every configured download is captured before context closure. Unsupported shapes are reported, not silently skipped. |
| REV-03 | Use a disposable report with an ambiguous repeated label or positional/virtualized row locator. Attempt validation; repair using an exact label/text or stable CSS in review. | Unsafe/unsupported locator actions cannot activate as-is. Repair preserves the frame chain. An ambiguous locator still fails rather than choosing an arbitrary match. Record failure and repaired retest separately. |
| REV-04 | On a draft copy, set a wrong exact report title; then a wrong required output column/period. For an asynchronous report configure its real generation action and loading cycle/changed-result signal; compare against an old export. | Wrong identity/schema/period fails before publication, transformation or SQL. Generation must reach the configured completion signal. A clickable export or plausible old workbook alone is insufficient. Keep evidence of each rejection; do not manufacture stale production data. |
| REV-05 | Queue a test run at revision A. Save a draft revision B with a visible harmless parameter change while A is queued. Validate/activate B and run again. | Queued A retains its revision/parameters; the new run uses B. Editing drafts or portable files cannot silently change A. An old core revision requires resave/revalidation after update. |

## Dates and multiple files

For batching, record **one range including writes to both date inputs**, not
eleven separate interactions. In review, name the parameters `start` and `end`,
set the entire desired range, then enable **Download in date batches**, select
the two parameter names and set **Weeks per file batch** to `10`.

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| DATE-01 | In a draft with both date input steps, set start to Fixed date and end to Portal default (leave untouched). Run and inspect the portal/end value and execution evidence. | Replay writes the fixed start and omits the end write. Resolved portal end is read/logged when available. Output period matches these values. This unbatched case does not require a calculated end. |
| DATE-02 | Set a date to Calculated date: today, yesterday or a period boundary; save the explicit Flow timezone. Compare logged resolution to that timezone. Exercise leap day/month/year boundary with synthetic fixtures or an explicitly fixed test clock. | One resolution per run, valid formatting/range, no worker-timezone drift. Do not change the work PC's system clock to force this test. Boundary fixture results cannot substitute for a live timezone check. |
| DATE-03 | Configure start `2025-01-01`, end `2026-12-31`, format `%Y-%m-%d`, batch size 10. Inspect portable `--dry-run` first, then run on a suitable test report. | Eleven inclusive ranges, each at most 70 days: first `2025-01-01`–`2025-03-11`, last `2026-12-02`–`2026-12-31`. No gaps/overlaps; one output per range for a one-download recording. File names have distinct part numbers. Compare actual report filters and output periods, not just names. |
| DATE-04 | Repeat with two recorded outputs and a small three-range interval, e.g. `2025-01-01` through `2025-06-01` at 10 weeks. Enable a simple Python transformation in the test flow. | Six acquired outputs and the configured transformed results. Every range repeats the recording from a fresh page; bundle identity includes range and step. All downloads validate before publication/transformation/SQL begin. |
| DATE-05 | On draft copies try reversed dates, the same boundary parameter twice, Portal default as a boundary, batch size 0 or 53, and a range requiring more than 500 batches. | Save/validation/run preparation rejects invalid configurations before accepted outputs. Calendar date formats supported by review are `%Y-%m-%d`, `%d/%m/%Y`, `%m/%d/%Y`, `%Y%m%d`; ISO week codes are not implemented. |
| DATE-06 | In a controlled fixture/test report, fail the last range's download or output validation after earlier ranges succeed. Inspect destination, transformation and SQL logs. Resume only through supported recovery; repeat with a changed portal default. | Incomplete bundle is not published/transformed/loaded. Recovery retains original resolved dates and replays required session steps. Changed defaults that prevent reproducing that context require a new run; partial old/new date bundles never count as success. |

## GSCM bookmark method and recorded method

Keep diagnostic rendered-grid mode **off** for normal-path tests. Exact module
and scope coverage lives in Flows → Settings. See [GSCM behavior](../../../recorded_flows.md)
for load-event limitations; an unqualified compatibility wait is still possible.

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| GSCM-01 | Scan configured modules and each available Private/Public/Custom scope. Compare the resulting stable IDs, raw names and scopes to the prepared 300+ inventory, including filtered and collapsed-folder entries. Repeat three times from fresh sessions. | Complete coverage consistently matches the expected identities. Routine discovery reads datasets without scrolling through all rows. Loaded rows hidden by filters are included; unprovided server rows cannot be claimed discovered. Record expected/found/missing/extra counts and coverage status per scope. |
| GSCM-02 | Restrict a scan to one scope/module. Then use a disposable configuration with a missing scope or unavailable module, or a controlled delayed/failed load. Compare catalog before/after. Restore configuration. | Complete restricted coverage is distinct from a complete whole catalog. Missing/loading/unavailable/empty states are distinguishable. Unproven or failed coverage is incomplete; verified entries may update but unseen existing bookmarks are retained. An empty/failed scan never silently clears the catalog. |
| GSCM-03 | Run first, middle, last and previously failing bookmarks. Change the displayed sort/filter order and repeat; include duplicate labels in different scopes. | Correct stable ID/raw name/scope resolves in the current bound dataset. No scrolling search sequence; native selection may reveal a row. Correct report identity, readiness and export verified each time. Record IDs and selected/report identity evidence privately. |
| GSCM-04 | In test bookmarks, rename/delete an ID or arrange a stale/wrong selection, then run the saved flow. Use synthetic cases for modifications unavailable on the live portal. | Identity mismatch/deleted bookmark/selection rejection cannot silently export a different report. Run exposes an actionable reason and no wrong output is accepted. |
| GSCM-05 | Create one GSCM Download bookmark flow and a separate Recorded flow from a known report route; record/review/validate the latter without relying on a successful full catalog scan. | Both authoring methods are available. Existing bookmark flows retain their method. A recording with virtual row IDs requires locator repair; use bookmark authoring when reliable stable row interaction is unavailable. |

## Browser settings, worker capacity and motion

Use isolated outputs for concurrent tests. A 32-slot setting is a ceiling, not
evidence that the portals can handle 32 simultaneous reports.

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| BROWSER-01 | Validate/run a supported test recording in Chrome, switch the global browser to Edge, then create/save/validate the appropriate new revision and repeat. Queue one job before the switch and another afterward. | Same Flow/action model works with both browsers. New jobs use the selected browser; queued/running jobs retain the captured browser. Separate profile state may require sign-in. No per-browser code copy. Compare report data/schema/periods and timings for both. Regenerate exported scripts when changing their saved browser. |
| BROWSER-02 | On an isolated test worker where the selected browser is unavailable, request a run; separately test an expired session/MFA challenge and attempted concurrent use of the same profile. | Missing browser has a named launch error, no silent fallback. Authentication interruption is visible and counted separately. Concurrent profile use is refused; another worker's credentials/profile are not borrowed. Keep auth data out of evidence. |
| CAP-01 | Set shared total 2, background 2, visible 2 and suitable portal limits on the test pool. Queue independent mixed-mode flows and a scan/recording. Watch active reservations; lower shared total to 1 while two operations are active. | Shared total is enforced across both modes including scans/recording/validation/finalization. Lowering limits lets current work drain and restricts new claims. A recording remains sequential. Restore settings after the case. |
| CAP-02 | With installed/authenticated slots, test mode and per-portal limits independently. Use a catalog flow with three exports and parallel downloads 3, then cancel a helper. Inspect coordinator/task identities and bundle publication. | All three limits apply. Coordinator doing its own download counts once, helpers consume extra slots. No partial bundle publishes; cancellation targets the assigned worker. File/Outlook/recorded acquisition does not gain within-recording parallelism. |
| CAP-03 | In the controlled maintenance window, verify slot 1 and an added slot retain expected service/task/profile names after setup. Inspect registration through slot 32 without launching 32 portal jobs. With disposable active work, verify update coordination. | Existing profiles/settings preserved, missing service enrollment reported, headed tasks launch hidden consoles with logs. Updates wait for work/reservations and stop all installed slots before replacing code. No old worker continues publishing late results after replacement. |
| CAP-04 | After correctness checks, compare comparable isolated workloads at shared limits 8, 12, 16, then 24 only when lower levels remain reliable. Keep portal limits 2–4 initially; separately tune them if justified. | Record successful reports/hour, first-attempt success, recovered success, auth interruptions, median/p95 latency, peak RAM and sustained CPU. Select capacity from successful throughput and correctness. Stop increasing when errors/latency worsen; 5090/96 GB/9900X3D alone does not establish a safe count. |
| UI-01 | Observe an active worker icon. Then enable reduced motion in OS/browser and repeat. | Active worker displays a moving robot icon. Reduced-motion preference makes it static. Activity state remains understandable without animation. |

## Portable pipeline, recovery and existing-flow regression

Use [portable script instructions](../../../recorded_flows.md) for supported
arguments and credential setup. A copied script uses its saved stages by
default; explicitly isolate its destinations before running it.

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| PORT-01 | Activate a validated recording with a Python transformation. Inspect `Scripts/run_flow.py`, `Scripts/versions/` and manifest; copy only the generated script outside Metronome. In a separate environment with its declared dependencies/browser, run `python -I run_flow.py --dry-run`. | Script contains readable config, steps and helpers with generator/revision/dependency/hash metadata. Dry run reports stages/parameters/batches without auth, report queries or SQL. No import of Metronome, DB or adjacent execution config is needed. |
| PORT-02 | On that copy, supply its own credentials and isolated `--output-root` and `--profile-dir`; run headed and headless against the test report. Compare acquisition and transformed outputs to a scheduled run. Repeat DATE-04 with the portable script. | Equivalent report identity, periods, schema, row counts and transformations; six downloads for that batch case. Auth state is not embedded. All saved stages execute unless explicitly disabled. Record machine/dependencies and comparison results. |
| PORT-03 | Use `--parameter start=...`, `--no-transform` and `--no-sql` on disposable copies. Attempt validation of a transformation with an unavailable local import/resource. Edit a generated launcher, then request regeneration. | Overrides affect the intended stage/parameter; unsupported external dependencies prevent a portability claim. Modified script is preserved/marked modified rather than overwritten. Edits do not alter scheduled execution or immutable revisions. |
| RECOVERY-01 | Using a test worker, interrupt a run after a verified download; resume it after worker restart/lease expiry. Attempt a second run using the same managed output/profile. | Session-dependent steps replay with frozen parameters, only verified compatible downloads may be reused, and locks prevent overlapping ownership. Expired workers cannot publish late output. Capture original/resumed run IDs. |
| SQL-01 | On a dedicated test table, run the complete download → validation → publication → Python transform → SQL pipeline. Compare transformed inputs and loaded rows/types; retry a bundle with a missing or duplicate range using a fixture. | Saved stage order and artifact roles preserved. Complete bundle loads according to configured SQL behavior. Missing/duplicate batch ranges and missing transformed outputs block retry. Validation-only runs must not execute SQL. |
| SQL-02 | Use the automated SQL-outcome fixture or an isolated test database to simulate connection loss around commit. Inspect unknown-outcome marker/state; attempt retry and portable `--no-sql`. | Unknown commit outcome blocks automatic retry until explicitly reconciled against database evidence. `--no-sql` does not clear an unresolved marker. Never force this on a production table or remove a marker without reconciliation. |
| REG-01 | Run one previously working local-file flow, one Outlook flow and one existing catalog/bookmark flow into test destinations, with their existing transformation/SQL settings where applicable. | Acquisition behavior and selected execution methods unchanged; expected artifacts/stage order retained. File/Outlook/SQL-only work does not launch a portal browser merely because the global browser changed. Compare to baseline. |

## Run the automated checks

Use an isolated development checkout, not the live worker installation. Match
the Python 3.13 test dependencies in the
[CI workflow](../../../../.github/workflows/tests.yml); install the required
browser fixtures using `python -m playwright install --with-deps chromium chrome msedge`.
Normal application setup uses [requirements.txt](../../../../requirements.txt);
pytest is an additional development dependency. CI intentionally records its
own explicit test dependency set.

```powershell
# Focused recording/control/date/browser/capacity regressions
python -m pytest tests/test_recording_controls.py tests/test_recording_date_batches.py tests/test_flow_recordings.py tests/test_recorded_browser_pipeline.py tests/test_flow_capacity.py tests/test_flow_parallel.py tests/test_flows.py tests/test_gscm_dataset_inventory.py tests/test_gscm.py -q

# Required for shared execution changes
python -m pytest tests -q

# All current frontend suites, stopping at a failure
foreach ($testFile in (Get-ChildItem -LiteralPath tests -Filter 'test_*.mjs' | Sort-Object Name)) {
    node $testFile.FullName
    if ($LASTEXITCODE -ne 0) { throw "Failed: $($testFile.Name)" }
}
node --check app/static/app.js
node --check app/static/flow_run_log.js
node --check app/static/flow_recordings.js
```

Capture each command's exit code/output. Keep pytest temporary folders outside
the checkout; managed Flow paths intentionally reject code directories.
Test-file coverage is mapped in the [report](test-report.md).

## Acceptance, evidence and cleanup

- Run each representative pilot recording **three times from fresh browser
  contexts**, with parameter variation where applicable. Duplicate its case
  rows for every browser/attempt. Compare data/periods/structure, not workbook
  byte equality when metadata changes.
- Require correct identity, complete outputs and intended period for every
  accepted run. Any wrong report, unverified freshness, partial publication,
  duplicate SQL load or unresolved cancellation is a failure to investigate.
  Preserve failed evidence and retest after a fix/new revision.
- Record first-attempt success as correct first attempts / eligible attempts
  when portal and auth are available. Also report all attempts, portal outages,
  authentication interruptions and recovered successes separately. Keep sample
  counts and time window; three pilot passes cannot establish 99% reliability.
- Restore saved browser/capacity/coverage settings, disable test schedules and
  finish/cancel test reservations. Retain protected evidence; remove only
  designated disposable outputs/tables through normal maintenance procedures.
- If a recording cannot be qualified, keep it disabled and continue using its
  existing bookmark/catalog method. For a release regression, pause affected
  schedules and use the normal reviewed update/rollback process with the prior
  known-good code and matching worker versions. Do not downgrade running workers
  or silently activate an old execution-core recording revision.

No HTTP replay or MicroStrategy API accelerator is being qualified here.
Recorded execution keeps unqualified replay disabled. Live completion signals,
enterprise SSO and actual maximum portal concurrency remain pilot questions.
