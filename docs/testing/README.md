# Testing instructions and reports

Every merge to `main` carries a reviewable test plan and results report. This
is a standing instruction from the repository owner, recorded in
[AGENTS.md](../../AGENTS.md) and the
[PR template](../../.github/PULL_REQUEST_TEMPLATE.md). Application CI does not
check these documents; the PR checklist does.

## Start here

The [release index](releases/INDEX.md) lists every package, newest first.
Templates: [test plan](templates/test-plan.md) and [test report](templates/test-report.md).

## For each merge to main

1. Create `docs/testing/releases/YYYY-MM-DD-short-topic/` (add the PR number
   if a same-day name would collide), copy both templates, and add a row to
   the [release index](releases/INDEX.md). Related follow-up PRs may update a
   package if each PR's scope, tested revision and results stay separately
   attributable.
2. Write the plan while implementing. Cover new behavior, affected existing
   behavior and realistic failures. Give cases stable IDs, explicit steps,
   observable expected results and evidence requirements. Include deployment
   prerequisites and cleanup. Keep a docs-only plan proportional.
3. Run one smallest non-overlapping affected test set plus syntax checks
   through the verifier (below); final-head CI supplies the full regression
   for application, dependency and test changes, and the lightweight scope
   gate covers documentation, policy, template and workflow-only changes.
   Record exact commands, UTC date, code SHA, OS, runtime and browser
   versions, counts and evidence. A passing historical run keeps its original
   SHA.
4. Commit the plan and report with the implementation and link them in the
   PR. A report holds the evidence available at its stated cutoff and marks
   later CI as pending. After the final checks, record the run URL, head SHA
   and result in the **PR testing section** rather than amending the commit.
   Any code change invalidates older final-head evidence.
5. Merge after the final required checks pass, pinned to that head. The PR
   records the merge SHA. In the delivery reply, link the plan and report and
   state the delivery state.

## Verifier

Windows:

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Preflight
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flows.py::test_safe_output_path_never_overwrites `
  -SyntaxPath app/flow_worker.py
```

Linux, macOS and containers (the same modes, rules and `result.json`):

```bash
python tools/check.py setup
python tools/check.py preflight
python tools/check.py verify \
  --test tests/test_flows.py::test_safe_output_path_never_overwrites \
  --syntax app/flow_worker.py
```

Setup creates `.venv` from Python 3.13 and installs the exact
`requirements-ci.lock`. Every invocation records a compact JSON result under a
unique ignored `.test-runs/` directory. Verification sets isolated database,
temporary, browser-profile and Flow-root paths before application imports, so
local runs never touch a real installation. Pass `-Reuse` / `--reuse` to reuse
a matching successful result; the verifier compares the source fingerprint.
A local full run is a diagnostic and requires `-Full -DiagnosticReason` /
`--full --diagnostic-reason`.

After a failure, rerun the failed case and the integration companions it
needs. Where PowerShell is unavailable, Playwright's `chrome` channel may also
be absent; cases that need it are covered by final-head CI, which installs
both `chromium` and `chrome`.

## Merge gate and delivery states

Every workflow execution ends in one check named `Merge ready`. It accepts an
omitted job only when change classification explicitly excluded that job, and
rejects failed, cancelled, missing or unexpectedly skipped selected work. The
PR must contain current `main`, and its testing section must record the final
run URL and tested head SHA before a head-pinned merge.

Report delivery as one of four states: implementation ready, local checks
complete, CI complete, merged. A merge is not a deployment.

## Live testing is opt-in only

Work-PC, live portal, authentication and hardware checks happen only when the
owner asks for them in the current task. When not requested, leave live cases
and their status rows out of the plan, report, PR and reply. When requested,
append dated results with deployed revisions and sanitized evidence,
preserving earlier failures and retests. Never invoke a Metronome live-fix
skill for testing unless the owner names it in that task.

## Result meanings

| Status | Meaning |
| --- | --- |
| PASS | The specified check ran on the stated revision/environment and matched its expected result. |
| FAIL | It ran and an expected result was not met; link a defect and evidence. |
| BLOCKED | A named prerequisite prevents execution; record the reason and next action. |
| NOT RUN | No execution evidence exists yet. Correct for in-scope automated checks still pending, such as final-head CI. |
| N/A | The case does not apply to this release/environment; explain why. It does not count as PASS. |

Keep **automated**, **synthetic browser**, explicitly requested **work-PC
live** and **load benchmark** results separate. Report skips, warnings and
known limitations. CI installation of Chrome/Edge does not itself prove portal
SSO or browser equivalence.

## Evidence and repeatability

For explicitly requested live attempts store case ID, UTC timestamp, tester,
app/worker SHA, browser version, run/session ID, expected versus actual
result, status and a sanitized evidence reference. Duplicate a row for another
browser, revision or attempt; never overwrite a failed attempt with a pass.
Screenshots and workbook comparisons with business data belong in the existing
protected storage; publish only the sanitized finding and an opaque evidence
ID to GitHub.

Preserve useful aggregate CI evidence in the report because hosted logs
expire. Do not upload credentials, protected browser state, private report
routes, report workbooks or raw Playwright traces. Record what was compared
(identity, columns, periods, row counts) without publishing the data.

Canonical automated checks live in
[.github/workflows/tests.yml](../../.github/workflows/tests.yml), the source of
truth for the CI environment as dependencies change.
