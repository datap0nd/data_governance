# <Release>: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: <link>
- Evidence cutoff (UTC): <timestamp/date>
- Tested code revision: <exact SHA; identify any uncommitted files tested>
- Environment: <OS, Python/Node, browser/Playwright versions as applicable>
- Overall finding: <automated/synthetic finding; add a live finding only when explicitly requested>

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| <ID> | <Actual command> | <SHA and environment> | <PASS/FAIL; counts, skips, warnings> | <CI run/job, `.test-runs/<id>/result.json` from either verifier, or protected reference> |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| <ID> | NOT RUN | <Why an explicitly planned check was not performed> | <Required environment/action> |

<This table is only for checks that are in scope and unfinished, such as
final-head CI. Checks that were never in scope are omitted from the report.>

## Usability evidence

<Omit this section entirely when no user interface changed.>

## Findings, limitations and retests

<Describe failures, their impact and evidence of any successful retest.
Retain the original result. Explain skips/warnings and limits of synthetic data.
For explicitly requested manual results, include timestamp, tester, app/worker
revisions, browser, run/session ID, actual result and sanitized evidence
reference per attempt.>

## Merge evidence

<Final CI may finish after this file is committed. Until then say pending.
Before merging, update the PR testing section with the final run URL, tested
head SHA and result. Link the PR here; its merge record supplies the merge SHA.
Do not pretend pre-merge evidence is post-deployment verification.>
