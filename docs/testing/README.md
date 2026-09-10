# Testing instructions and reports

Every merge to `main` must have a reviewable testing guide and results report.
This is a standing instruction from the repository owner, recorded in
[AGENTS.md](../../AGENTS.md), [CLAUDE.md](../../CLAUDE.md) and the
[PR template](../../.github/PULL_REQUEST_TEMPLATE.md).

## Start here

| Release | What to test | Results |
| --- | --- | --- |
| 2026-09-11: NASCA direct pywin32 reads | [Test plan](releases/2026-09-11-nasca-direct-com-read/test-plan.md) | [Test report](releases/2026-09-11-nasca-direct-com-read/test-report.md) |
| 2026-09-11: browser filename handoff | [Test plan](releases/2026-09-11-browser-filename-handoff/test-plan.md) | [Test report](releases/2026-09-11-browser-filename-handoff/test-report.md) |
| 2026-09-10: NASCA recorded-path preservation | [Test plan](releases/2026-09-10-nasca-recorded-path/test-plan.md) | [Test report](releases/2026-09-10-nasca-recorded-path/test-report.md) |
| 2026-09-10: NASCA browser-source recovery | [Test plan](releases/2026-09-10-nasca-browser-source/test-plan.md) | [Test report](releases/2026-09-10-nasca-browser-source/test-report.md) |
| 2026-09-10: NASCA active Excel session | [Test plan](releases/2026-09-10-nasca-active-excel-session/test-plan.md) | [Test report](releases/2026-09-10-nasca-active-excel-session/test-report.md) |
| 2026-09-10: NASCA recording validation parity | [Test plan](releases/2026-09-10-nasca-recording-validation/test-plan.md) | [Test report](releases/2026-09-10-nasca-recording-validation/test-report.md) |
| 2026-09-10: delivery foundation and merge gate | [Test plan](releases/2026-09-10-delivery-foundation/test-plan.md) | [Test report](releases/2026-09-10-delivery-foundation/test-report.md) |
| 2026-09-10: NASCA Excel-COM Flow recovery | [Test plan](releases/2026-09-10-nasca-excel-com/test-plan.md) | [Test report](releases/2026-09-10-nasca-excel-com/test-report.md) |
| 2026-09-10: recorded ASAP staging completion | [Test plan](releases/2026-09-10-recorded-asap-staging-completion/test-plan.md) | [Test report](releases/2026-09-10-recorded-asap-staging-completion/test-report.md) |
| 2026-09-10: recorded ASAP long-preamble recovery | [Test plan](releases/2026-09-10-recorded-asap-long-preamble/test-plan.md) | [Test report](releases/2026-09-10-recorded-asap-long-preamble/test-report.md) |
| 2026-09-10: recorded download processing regression | [Test plan](releases/2026-09-10-recorded-download-processing-regression/test-plan.md) | [Test report](releases/2026-09-10-recorded-download-processing-regression/test-report.md) |
| 2026-09-10: modern Excel open recovery | [Test plan](releases/2026-09-10-modern-excel-open-recovery/test-plan.md) | [Test report](releases/2026-09-10-modern-excel-open-recovery/test-report.md) |
| 2026-09-09: range setting on one recorded step | [Test plan](releases/2026-09-09-range-step-advanced/test-plan.md) | [Test report](releases/2026-09-09-range-step-advanced/test-report.md) |
| 2026-09-09: semantic week ranges and Excel-family downloads | [Test plan](releases/2026-09-09-range-steps-excel-family/test-plan.md) | [Test report](releases/2026-09-09-range-steps-excel-family/test-report.md) |
| 2026-09-09: Flow classification and replace-mode SQL recovery | [Test plan](releases/2026-09-09-flow-classification-sql-recovery/test-plan.md) | [Test report](releases/2026-09-09-flow-classification-sql-recovery/test-report.md) |
| 2026-09-09: opt-in live testing and post-#92 scope review | [Test plan](releases/2026-09-09-testing-scope-post-92/test-plan.md) | [Test report](releases/2026-09-09-testing-scope-post-92/test-report.md) |
| 2026-09-09: faster pull-request CI | [Test plan](releases/2026-09-09-faster-ci/test-plan.md) | [Test report](releases/2026-09-09-faster-ci/test-report.md) |
| 2026-09-09: discoverable recording replacement and template retargeting | [Test plan](releases/2026-09-09-recording-replacement-retargeting/test-plan.md) | [Test report](releases/2026-09-09-recording-replacement-retargeting/test-report.md) |
| 2026-09-09: SQL table ownership and binary recordings | [Test plan](releases/2026-09-09-sql-table-owner/test-plan.md) | [Test report](releases/2026-09-09-sql-table-owner/test-report.md) |
| 2026-09-09: Save recorded Flow without testing | [Test plan](releases/2026-09-09-save-recording-without-test/test-plan.md) | [Test report](releases/2026-09-09-save-recording-without-test/test-report.md) |
| 2026-09-09: recording dialog import | [Test plan](releases/2026-09-09-recording-dialog-import/test-plan.md) | [Test report](releases/2026-09-09-recording-dialog-import/test-report.md) |
| 2026-09-08: Users, TMDL Checker retirement and refresh recovery | [Test plan](releases/2026-09-08-users-refresh-reliability/test-plan.md) | [Test report](releases/2026-09-08-users-refresh-reliability/test-report.md) |
| 2026-09-07: Reusable recordings and downstream view refresh | [Test plan](releases/2026-09-07-reusable-recordings-view-refresh/test-plan.md) | [Test report](releases/2026-09-07-reusable-recordings-view-refresh/test-report.md) |
| 2026-09-07: setup shared SSO password | [Test plan](releases/2026-09-07-setup-sso-password/test-plan.md) | [Test report](releases/2026-09-07-setup-sso-password/test-report.md) |
| 2026-09-07: Pipeline explanation quality gate | [Test plan](releases/2026-09-07-pipeline-explanation-quality/test-plan.md) | [Test report](releases/2026-09-07-pipeline-explanation-quality/test-report.md) |
| 2026-09-07: Pipelines edges routed around cards | [Test plan](releases/2026-09-07-pipeline-edge-routing/test-plan.md) | [Test report](releases/2026-09-07-pipeline-edge-routing/test-report.md) |
| 2026-09-07: GSCM bookmark fallback navigation and import suggestion | [Test plan](releases/2026-09-07-gscm-bookmark-fallback/test-plan.md) | [Test report](releases/2026-09-07-gscm-bookmark-fallback/test-report.md) |
| 2026-09-06: GSCM bookmark recording | [Test plan](releases/2026-09-06-gscm-bookmark-recording/test-plan.md) | [Test report](releases/2026-09-06-gscm-bookmark-recording/test-report.md) |
| 2026-09-06: Luna GSCM investigation prompt | [Test plan](releases/2026-09-06-luna-gscm-probe/test-plan.md) | [Test report](releases/2026-09-06-luna-gscm-probe/test-report.md) |
| 2026-09-06: automatic Flow files | [Test plan](releases/2026-09-06-automatic-flow-files/test-plan.md) | [Test report](releases/2026-09-06-automatic-flow-files/test-report.md) |
| 2026-09-06: recorded click dispatch and technical logs | [Test plan](releases/2026-09-06-recording-click-dispatch/test-plan.md) | [Test report](releases/2026-09-06-recording-click-dispatch/test-report.md) |
| 2026-09-06: recording playback and diagnostics | [Test plan](releases/2026-09-06-recording-playback/test-plan.md) | [Test report](releases/2026-09-06-recording-playback/test-report.md) |
| 2026-09-06: optional recording checks | [Test plan](releases/2026-09-06-optional-recording-checks/test-plan.md) | [Test report](releases/2026-09-06-optional-recording-checks/test-report.md) |
| 2026-09-06: recording worker startup | [Test plan](releases/2026-09-06-recording-startup/test-plan.md) | [Test report](releases/2026-09-06-recording-startup/test-report.md) |
| 2026-09-06: managed Flow editor | [Test plan](releases/2026-09-06-managed-editor/test-plan.md) | [Test report](releases/2026-09-06-managed-editor/test-report.md) |
| 2026-09-06: intuitive recording | [Test plan](releases/2026-09-06-intuitive-recording/test-plan.md) | [Test report](releases/2026-09-06-intuitive-recording/test-report.md) |
| 2026-09-05: visual recording editor, increment 3 | [Start here: record, review, test and schedule](releases/2026-09-05-visual-recording-editor/test-plan.md) | [Test report](releases/2026-09-05-visual-recording-editor/test-report.md) |
| 2026-09-05: recording v2, redesign increment 2 | [Compatibility instructions](releases/2026-09-05-recording-v2/test-plan.md) | [Test report](releases/2026-09-05-recording-v2/test-report.md) |
| 2026-09-05: Dubai time, redesign increment 1 | [Time-policy instructions](releases/2026-09-05-dubai-time/test-plan.md) | [Test report](releases/2026-09-05-dubai-time/test-report.md) |
| 2026-09-05: Flows, PRs #67–#69 | [Work-PC test plan](releases/2026-09-05-flows/test-plan.md): recording controls, dates, GSCM, browser settings, workers and portability | [Verified automated report](releases/2026-09-05-flows/test-report.md); [live results worksheet](releases/2026-09-05-flows/manual-results.csv), initially NOT RUN |
| 2026-09-05: testing documentation process, PR #70 | [Documentation checks](releases/2026-09-05-testing-process/test-plan.md) | [Documentation report](releases/2026-09-05-testing-process/test-report.md) |

Historical releases retain their original tests and results; the older
date-batching tests describe that historical release, not current supported
execution. Historical work-PC instructions are not a standing requirement.

## For each future main merge

1. Create `docs/testing/releases/YYYY-MM-DD-short-topic/` (add the PR number
   if a same-day name would collide). Copy the [plan](templates/test-plan.md)
   and [report](templates/test-report.md) templates and add the package above.
   Related follow-up PRs may update a package if each PR's scope, tested revision
   and results remain separately attributable.
2. Write the plan while implementing. Cover new behavior, affected existing
   behavior and realistic failures. Give cases stable IDs, explicit steps,
   observable expected results and evidence requirements. Include deployment
   prerequisites and cleanup. Keep a docs-only plan proportional.
3. Run one smallest non-overlapping affected test set locally, plus applicable
   syntax checks. Do not then run a broader local suite containing the same
   cases. Final-head CI supplies the full Python regression for application,
   dependency and test changes. Documentation, repository-policy, PR-template
   and workflow-only changes use the lightweight scope gate. After rebasing,
   rerun application tests only if application/test code changed or a related
   conflict was resolved. Record exact commands, UTC date, code SHA, OS,
   runtime/browser versions, counts and evidence. A passing historical run must
   retain its original SHA.
4. Commit the plan/report with the implementation and link them in the PR.
   Reports contain evidence available at their stated cutoff. Mark subsequent
   CI as pending until it finishes. After final checks, update the **PR testing
   section** with its final run URL, head SHA and result before merging; this
   avoids repeatedly changing the commit just to document its own CI results.
   Any code change invalidates older final-head evidence and needs new checks.
5. Merge after final required checks pass. The PR records the actual merge SHA;
   do not predict a squash SHA. In the delivery reply link the plan/report and
   state merge status.

## Supported local command

The repository has one local test entry point:

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Preflight
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flows.py::test_safe_output_path_never_overwrites `
  -SyntaxPath app/flow_worker.py
```

Setup creates `.venv` from Python 3.13 and installs the exact
`requirements-ci.lock`. Every invocation records a compact JSON result under a
unique ignored `.test-runs/` directory. Verification sets isolated database,
temporary, browser-profile and evidence paths before application imports.
Pass `-Reuse` only when reusing a matching successful result. A local full run
is reserved for a named diagnostic or equivalence investigation and requires
both `-Full` and `-DiagnosticReason`.

After a failure, rerun the failed case and only the integration companions it
needs. Do not restart the suite merely to obtain a traceback. Review the actual
diff and its relevant failure/recovery paths once; repeat review only after a
new finding or code change. Write the plan/report during implementation and
open the PR after focused evidence is ready. Final full regression belongs to
CI.

## Merge gate and delivery states

Every workflow execution ends in one check named `Merge ready`. It accepts an
omitted job only when change classification explicitly excluded that job, and
rejects failed, cancelled, missing or unexpectedly skipped selected work. The
PR must contain current `main`, and its testing section must record the final
run URL and tested head SHA before a head-pinned merge.

Report delivery as four distinct states: implementation ready, local checks
complete, CI complete and merged. A merge is not a deployment.

## Live testing is opt-in only

Do not open, inspect, plan, attempt or report work-PC, live portal,
authentication or hardware checks unless the owner explicitly requests them in
the current task. If not requested, omit live cases and status placeholders
from the plan, report, PR and delivery reply. When explicitly requested, append
dated results with deployed revisions and sanitized evidence, preserving earlier
failures and retests. Never invoke a Metronome/live-fix skill for testing unless
the owner explicitly names or requests it in that task.

This is a repository delivery requirement and PR checklist. The application CI
does not independently enforce the presence or accuracy of these documents.

## Result meanings

| Status | Meaning |
| --- | --- |
| PASS | The specified check ran on the stated revision/environment and matched its expected result. |
| FAIL | It ran and an expected result was not met; link a defect and evidence. |
| BLOCKED | A named prerequisite prevents execution; record the reason and next action. |
| NOT RUN | No execution evidence exists yet. This is the default for live cases. |
| N/A | The case does not apply to this release/environment; explain why. It does not count as PASS. |

Keep **automated**, **synthetic browser**, explicitly requested **work-PC live**
and **load benchmark** results separate. Report skips/warnings and known
limitations. CI installation of Chrome/Edge does not itself prove portal SSO or
browser equivalence.

## Evidence and repeatability

When live testing was explicitly requested, use the release worksheet or the
report template. For each manual attempt store
case ID, UTC timestamp, tester, app/worker SHA, browser version, run/session ID,
expected versus actual result, status and a sanitized evidence reference.
Duplicate a row for another browser, revision or attempt; never overwrite a
failed attempt with a pass. Screenshots and workbook comparisons belong in the
existing protected storage when they contain business data. Publish only the
sanitized finding and opaque evidence ID to GitHub.

Preserve useful aggregate CI evidence in the report because hosted logs can
expire. Do not upload credentials, protected browser state, private report
routes, report workbooks or raw Playwright traces to GitHub. Record what was
compared (identity, columns, periods, row counts) without publishing the data.

Canonical automated checks live in [.github/workflows/tests.yml](../../.github/workflows/tests.yml).
The [Flows plan](releases/2026-09-05-flows/test-plan.md) gives copyable commands
for reproducing its relevant suites. The workflow remains the source of truth
for the CI environment as dependencies change.
