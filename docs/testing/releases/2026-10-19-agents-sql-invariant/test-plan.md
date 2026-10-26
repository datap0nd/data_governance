# 2026-10-19: AGENTS.md Flow SQL write invariant: test plan

- Change/PR: branch `claude/cool-bohr-x67q6x`; PR link in the PR's testing
  section. Rewords the AGENTS.md invariant on Flow SQL write credentials to
  match the owner SQL users merged in
  [datap0nd/data_governance#157](https://github.com/datap0nd/data_governance/pull/157).
  Documentation and repository policy only; no application change.
- Code baseline: `main` at `2f3ddbf`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container for the documentation checks; CI's
  documentation scope gate.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| D-01 | Compare the new invariant with `app/flow_sql.py` (`connection_credentials`, `load_artifacts`, `_engine`), `app/routers/flows.py` (`_build_job`, `_assert_sql_credentials`) and README "PostgreSQL is read-only" | Every clause matches the code: probe `PG*` credentials never write; writes use the `DG_UPLOAD_PG*` server; assigned Flows sign in as the owner; unassigned Flows use `DG_UPLOAD_PGUSER`; missing owner credentials fail closed with no fallback | File and line references in the report |
| D-02 | `git diff --check origin/main` and confirm the other policy files (`CLAUDE.md`, `GEMINI.md`) carry no conflicting copy | No whitespace errors; no other copy to update | Command output |
| D-03 | Final-head CI | `Change scope` classifies the change as documentation and `Merge ready` passes | PR testing section |

## Automated checks

```bash
git diff --check origin/main
grep -rn "DG_UPLOAD_PG" AGENTS.md CLAUDE.md GEMINI.md
```

## Acceptance and cleanup

Accepted when D-01 and D-02 pass and the final-head `Merge ready` check
passes. Rollback: revert the PR. Nothing to clean up.
