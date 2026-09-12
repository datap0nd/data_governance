# 2026-09-12 Gemini field-agent workflow and diagnosis bundle tool: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending (filled after the PR is opened)
- Evidence cutoff (UTC): 2026-09-12, before the PR commit
- Tested code revision: the uncommitted working tree that became the PR head; base `37169efe0cb33eb675224798fc9a1a33fde052b7`
- Environment: Linux x64 (remote coding container), Python 3.11.15, CI dependency set from `requirements-ci.txt` installed into a scratch virtual environment; no browser, no live portal, no work PC
- Overall finding: automated synthetic checks PASS; final-head CI pending at this cutoff

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-01 to T-05 | `python -m pytest tests/test_diagnose_run.py -q --tb=short` | working tree, Linux, Python 3.11.15 | **PASS: 5 passed**, 8.73 s | Console output of the run; not retained as a file |
| Syntax | `python -m py_compile tools/diagnose_run.py tests/test_diagnose_run.py` | same | PASS | Same session |
| T-06 | `tomllib` parse of the six command files, asserting the key set `{description, prompt}` | same | PASS, 6 files | Same session |
| T-07 | Manual link check of the new documents against the checkout | same | PASS | Reviewer inspection |

The supported local entry point `tools/check.ps1` requires a Windows checkout
with Python 3.13 and PowerShell; neither is available in the container, so
the focused set was executed with the equivalent `pytest` selection instead.
This is recorded as a deviation, not as a `check.ps1` result. Final-head CI on
Windows and Linux with Python 3.13 is the authoritative regression.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| none | | | |

Work-PC execution of the playbooks is deliberately outside this release's
scope; it is the owner-run observation phase described in
[docs/gemini_field_agent.md](../../../gemini_field_agent.md).

## Findings, limitations and retests

- During implementation the first version of the bundle failed for a Flow
  whose recording was still a draft, because the run-summary projection's
  recovery preflight raises an HTTP 409 in that state. The tool now records
  any projection it cannot compute as `{"unavailable": <reason>}` and still
  writes the bundle. T-01 seeds a saved recording so the preflight is
  exercised; T-02 covers the unavailable-comparison path.
- A Playwright "Call log:" tail survived the first redaction pass; strings are
  now cut at that marker before scrubbing, matching the existing recording
  diagnostics behavior. T-01 asserts the marker is absent.
- Synthetic data only: the redaction test proves the listed value classes
  are removed from a seeded run; it does not prove that every possible live
  event text is safe. The bundle is still to be reviewed before it is shared.

## Merge evidence

Pending. The PR testing section will record the final `Merge ready` run URL
and tested head SHA before the head-pinned merge; the PR's merge record
supplies the merge SHA.
