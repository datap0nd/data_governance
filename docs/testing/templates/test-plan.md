# <Release>: test plan

- Change/PR: <link; scope and user-visible behavior>
- Code baseline: <SHA; deployed app and worker versions to check>
- Related report: [test-report.md](test-report.md)
- Intended environments: <OS, runtime, browser or synthetic fixture; include a live environment only when explicitly requested>

## Prerequisites and test data

<How to prepare isolated fixtures and capture relevant settings. Include live
authentication or hardware prerequisites only when explicitly requested.>

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | <UI labels or copyable command; concrete inputs> | <Observable result, including data checks> | <Run ID, log summary or protected reference> |

<Cover changed behavior, negative/recovery paths and affected regressions.
Live cases are omitted unless explicitly requested.>

## Automated checks

<Exact commands (`tools/check.ps1` on Windows, `python tools/check.py`
elsewhere), runtime/dependency setup and scope. Never describe planned
execution as a completed result.>

## Acceptance and cleanup

<What makes this scope accepted; unresolved checks; restore settings, disable
test schedules and retain/delete only designated test artifacts as appropriate.
Give rollback guidance for a functional change.>
