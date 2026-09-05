# Testing instructions and reports

Every merge to `main` must have a reviewable testing guide and results report.
This is a standing instruction from the repository owner, recorded in
[AGENTS.md](../../AGENTS.md), [CLAUDE.md](../../CLAUDE.md) and the
[PR template](../../.github/PULL_REQUEST_TEMPLATE.md).

## Start here

| Release | What to test | Results |
| --- | --- | --- |
| 2026-09-05: visual recording editor, increment 3 | [Start here: record, review, test and schedule](releases/2026-09-05-visual-recording-editor/test-plan.md) | [Test report](releases/2026-09-05-visual-recording-editor/test-report.md) |
| 2026-09-05: recording v2, redesign increment 2 | [Compatibility instructions](releases/2026-09-05-recording-v2/test-plan.md) | [Test report](releases/2026-09-05-recording-v2/test-report.md) |
| 2026-09-05: Dubai time, redesign increment 1 | [Time-policy instructions](releases/2026-09-05-dubai-time/test-plan.md) | [Test report](releases/2026-09-05-dubai-time/test-report.md) |
| 2026-09-05: Flows, PRs #67–#69 | [Work-PC test plan](releases/2026-09-05-flows/test-plan.md): recording controls, dates, GSCM, browser settings, workers and portability | [Verified automated report](releases/2026-09-05-flows/test-report.md); [live results worksheet](releases/2026-09-05-flows/manual-results.csv), initially NOT RUN |
| 2026-09-05: testing documentation process, PR #70 | [Documentation checks](releases/2026-09-05-testing-process/test-plan.md) | [Documentation report](releases/2026-09-05-testing-process/test-report.md) |

For the redesigned recording editor, start with the first-work-PC sequence in
its guide above. Historical releases retain their original tests and results;
the older date-batching tests describe that historical release, not current
supported execution. Then complete the matrix for each affected portal.

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
3. Run appropriate checks. Shared execution changes require the full Python
   suite; frontend changes require affected Node tests and syntax checks.
   Record exact commands, UTC date, code SHA, OS, runtime/browser versions,
   counts and evidence. A passing historical run must retain its original SHA.
4. Commit the plan/report with the implementation and link them in the PR.
   Reports contain evidence available at their stated cutoff. Mark subsequent
   CI as pending until it finishes. After final checks, update the **PR testing
   section** with its final run URL, head SHA and result before merging; this
   avoids repeatedly changing the commit just to document its own CI results.
   Any code change invalidates older final-head evidence and needs new checks.
5. Merge after final required checks pass. The PR records the actual merge SHA;
   do not predict a squash SHA. In the delivery reply link the plan/report,
   state merge status and identify live checks still outstanding.
6. When work-PC results arrive, append a dated report/worksheet entry with the
   deployed app and worker revisions, case IDs and evidence. Preserve earlier
   failures and retests. A new results-only PR also documents its own scope.

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

Keep **automated**, **synthetic browser**, **work-PC live** and **load benchmark**
results separate. Report skips/warnings and known limitations. CI installation
of Chrome/Edge does not itself prove portal SSO or browser equivalence.

## Evidence and repeatability

Use the release worksheet or the report template. For each manual attempt store
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
