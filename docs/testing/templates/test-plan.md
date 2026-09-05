# <Release>: test plan

- Change/PR: <link; scope and user-visible behavior>
- Code baseline: <SHA; deployed app and worker versions to check>
- Related report: [test-report.md](test-report.md)
- Intended environments: <OS, runtime, browser, portal or fixture>

## Prerequisites and test data

<How to update, authenticate, prepare isolated inputs/destinations and capture
the original settings. Identify safe fixture data and external prerequisites.>

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | <UI labels or copyable command; concrete inputs> | <Observable result, including data checks> | <Run ID, log summary or protected reference> |

<Cover changed behavior, negative/recovery paths and affected regressions.
State which cases require live access and which can run locally.>

## Automated checks

<Exact commands, runtime/dependency setup and scope. Never describe planned
execution as a completed result.>

## Acceptance and cleanup

<What makes this scope accepted; unresolved checks; restore settings, disable
test schedules and retain/delete only designated test artifacts as appropriate.
Give rollback guidance for a functional change.>
