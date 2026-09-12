# Testing instructions and reports

Every merge to `main` must have a reviewable testing guide and results report.
[AGENTS.md](../../AGENTS.md) states the rule; this file owns the procedure, the
templates and the result vocabulary.

## Recent releases

| Release | What to test | Results |
| --- | --- | --- |
| 2026-09-12: agent guidance refresh and cross-platform verifier | [Test plan](releases/2026-09-12-agent-guidance-refresh/test-plan.md) | [Test report](releases/2026-09-12-agent-guidance-refresh/test-report.md) |
| 2026-09-12: Gemini field-agent workflow and diagnosis bundle tool | [Test plan](releases/2026-09-12-gemini-field-agent/test-plan.md) | [Test report](releases/2026-09-12-gemini-field-agent/test-report.md) |
| 2026-09-11: progress-based download and render waits; recorded-flow gate wording | [Test plan](releases/2026-09-11-progress-based-download-waits/test-plan.md) | [Test report](releases/2026-09-11-progress-based-download-waits/test-report.md) |
| 2026-09-11: optional recording tests | [Test plan](releases/2026-09-11-optional-recording-tests/test-plan.md) | [Test report](releases/2026-09-11-optional-recording-tests/test-report.md) |
| 2026-09-11: share copy settle wait and Outlook task command | [Test plan](releases/2026-09-11-share-copy-settle-outlook-task-command/test-plan.md) | [Test report](releases/2026-09-11-share-copy-settle-outlook-task-command/test-report.md) |

The complete history is in [the release index](releases/INDEX.md). Historical
releases retain their original tests and results; their older date-batching
tests and work-PC instructions describe that release, not current execution.

## For each future main merge

1. Create `docs/testing/releases/YYYY-MM-DD-short-topic/` (add the PR number if
   a same-day name would collide). Copy the [plan](templates/test-plan.md) and
   [report](templates/test-report.md) templates, and add a row to
   [the index](releases/INDEX.md) and to the table above. Related follow-up PRs
   may update a package if each PR's scope, tested revision and results remain
   separately attributable.
2. Write the plan while implementing. Cover new behavior, affected existing
   behavior and realistic failures. Give cases stable IDs, explicit steps,
   observable expected results and evidence requirements. Include deployment
   prerequisites and cleanup, and keep a docs-only plan proportional.
3. Run the focused local verification described in
   [AGENTS.md](../../AGENTS.md#verification) and record what actually ran:
   exact commands, UTC date, code SHA, OS, runtime/browser versions, counts,
   skips, warnings and evidence links. A passing historical run keeps its
   original SHA.
4. Commit the plan and report with the implementation and link them in the PR.
   Reports hold the evidence available at their stated cutoff; mark later CI as
   pending until it finishes. After the final checks, update the **PR testing
   section** with its run URL, head SHA and result before merging — that avoids
   changing the commit just to document its own CI. Any code change invalidates
   older final-head evidence.
5. Merge after the final required checks pass. The PR records the actual merge
   SHA; do not predict a squash SHA. In the delivery reply link the plan and
   report and state the merge status.

## Local commands

The verifier exists twice — `tools/check.py` for Linux, macOS and Windows,
`tools/check.ps1` for Windows PowerShell. They take the same selectors and write
the same result schema, and either is acceptable evidence; cite the
`result.json` path it prints.

```bash
python tools/check.py setup
python tools/check.py preflight
python tools/check.py verify \
  --test tests/test_flows.py::test_safe_output_path_never_overwrites \
  --syntax app/flow_worker.py
```

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Preflight
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flows.py::test_safe_output_path_never_overwrites `
  -SyntaxPath app/flow_worker.py
```

Setup builds `.venv` from Python 3.13 and installs the exact
`requirements-ci.lock`. Every invocation writes a compact JSON result under a
unique ignored `.test-runs/` directory, after pointing the database,
browser-profile and evidence paths there so nothing touches real state.
`check.py` keeps pytest's temporary root in a per-run scratch directory outside
the checkout — the application refuses a Flows root inside it — and removes that
scratch when the run passes, keeping it for diagnosis when it fails.
[AGENTS.md](../../AGENTS.md#verification) carries the selection rules (focused
sets, `--full`/`-Full` diagnostics, `--reuse`/`-Reuse`).

Node contract tests run directly: `node tests/test_flow_builder_contract.mjs`.

## Merge gate and delivery states

Every workflow execution ends in one check named `Merge ready`. It accepts an
omitted job only when change classification explicitly excluded that job, and
rejects failed, cancelled, missing or unexpectedly skipped selected work. The PR
must contain current `main`, and its testing section must record the final run
URL and tested head SHA before a head-pinned merge.

Report delivery as four distinct states: implementation ready, local checks
complete, CI complete and merged. A merge is not a deployment.

## Live testing is opt-in only

Work-PC, live portal, authentication and hardware checks happen only when the
owner requests them in the current task. When they are not requested, leave them
out of the plan, report, PR and reply entirely — no placeholder rows. When they
are requested, append dated results with deployed revisions and sanitized
evidence, preserving earlier failures and retests. Never invoke a
Metronome/live-fix skill for testing unless the owner names it in that task.

This is a repository delivery requirement and PR checklist. The application CI
does not independently enforce the presence or accuracy of these documents.

## Result meanings

| Status | Meaning |
| --- | --- |
| PASS | The specified check ran on the stated revision/environment and matched its expected result. |
| FAIL | It ran and an expected result was not met; link a defect and evidence. |
| BLOCKED | A named prerequisite prevents execution; record the reason and next action. |
| NOT RUN | No execution evidence exists yet. Correct for an in-scope automated check still waiting on final-head CI. |
| N/A | The case does not apply to this release/environment; explain why. It does not count as PASS. |

NOT RUN and BLOCKED describe work that is in scope and unfinished. A check that
was never in scope — unrequested live testing above all — is omitted, not
recorded as a placeholder.

Keep **automated**, **synthetic browser**, explicitly requested **work-PC live**
and **load benchmark** results separate. Report skips, warnings and known
limitations. CI installation of Chrome/Edge does not itself prove portal SSO or
browser equivalence.

## Evidence and repeatability

When live testing was explicitly requested, use the release worksheet or the
report template. For each manual attempt store case ID, UTC timestamp, tester,
app/worker SHA, browser version, run/session ID, expected versus actual result,
status and a sanitized evidence reference. Duplicate a row for another browser,
revision or attempt; never overwrite a failed attempt with a pass. Screenshots
and workbook comparisons belong in the existing protected storage when they
contain business data. Publish only the sanitized finding and opaque evidence ID
to GitHub.

Preserve useful aggregate CI evidence in the report because hosted logs can
expire. Do not upload credentials, protected browser state, private report
routes, report workbooks or raw Playwright traces to GitHub. Record what was
compared (identity, columns, periods, row counts) without publishing the data.

Canonical automated checks live in [.github/workflows/tests.yml](../../.github/workflows/tests.yml),
which remains the source of truth for the CI environment as dependencies change.
