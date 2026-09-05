# <Release>: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: <link>
- Evidence cutoff (UTC): <timestamp/date>
- Tested code revision: <exact SHA; identify any uncommitted files tested>
- Environment: <OS, Python/Node, browser/Playwright versions as applicable>
- Overall finding: <automated finding and live finding separately>

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| <ID> | <Actual command> | <SHA and environment> | <PASS/FAIL; counts, skips, warnings> | <CI run/job or protected reference> |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| <ID> | NOT RUN | <No live execution yet> | <Required test environment/action> |

## Findings, limitations and retests

<Describe failures, their impact and evidence of any successful retest.
Retain the original result. Explain skips/warnings and limits of synthetic data.
For manual results, include timestamp, tester, app/worker revisions, browser,
run/session ID, actual result and sanitized evidence reference per attempt.>

## Merge evidence

<Final CI may finish after this file is committed. Until then say pending.
Before merging, update the PR testing section with the final run URL, tested
head SHA and result. Link the PR here; its merge record supplies the merge SHA.
Do not pretend pre-merge evidence is post-deployment verification.>
