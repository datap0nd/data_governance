# 2026-10-19: AGENTS.md Flow SQL write invariant: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-19 08:25
- Tested revision: `main` `2f3ddbf` plus the uncommitted AGENTS.md edit and
  this package (documentation only)
- Environment: Linux container, git
- Overall finding: the new wording matches the merged code; documentation
  checks pass. Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result | Evidence |
| --- | --- | --- | --- | --- |
| D-01 | Read the new invariant against the code paths below | `2f3ddbf` | PASS. Assigned Flows freeze `connect_as` (`app/routers/flows.py`, `_build_job`); `connection_credentials` returns None only without `connect_as` and raises when the owner's SQL username or password is missing (`app/flow_sql.py`); `load_artifacts` uses the shared `_engine(database)` only when it returned None; `_engine` connects to `UPLOAD_PGHOST` with the owner's credentials or `UPLOAD_PGUSER`; queuing is refused by `_assert_sql_credentials`; the read-only `PG*` probe credentials are not used for writes (README "PostgreSQL is read-only") | Source references above |
| D-02 | `git diff --check origin/main`; `grep -rn "DG_UPLOAD_PG" AGENTS.md CLAUDE.md GEMINI.md` | Same | PASS: no whitespace errors; only AGENTS.md states the invariant (CLAUDE.md imports it) | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| D-03 | NOT RUN | Final-head CI has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

None. No application code, tests or behavior change.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
