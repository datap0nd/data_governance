# Python scheduler plan set: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: plans-only PR for `docs/plans/python_scheduler/`; the PR records
  its number and final head. At the owner's direction the PR stays open for
  Codex, which implements the stages from it.
- Evidence cutoff (UTC): 2026-09-24 07:45.
- Tested revision: working tree on `main` `a3a3b62` with the plan set staged;
  the PR's final head is recorded in its testing section.
- Environment: Linux agent container, Python 3.13 (system `python3` for the
  documentation scripts), Git.
- Overall finding: documentation checks pass; final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result | Evidence |
| --- | --- | --- | --- | --- |
| D-01 | `git diff --cached --check` over the staged plan set, package and index rows | Staged tree on `a3a3b62`; Linux | PASS: no whitespace errors. | Command output |
| D-02 | Relative-link resolver (a 20-line script kept outside the repository) over `docs/plans/python_scheduler/*.md`, this package, `docs/testing/releases/INDEX.md` and `docs/testing/README.md` | Staged tree on `a3a3b62`; Linux | PASS: checked 210 relative links in 9 files; 0 broken. | Script output |
| D-03 | Path checker (outside the repository) extracting every backticked repository path from the four plan files | Staged tree on `a3a3b62`; Linux | PASS: checked 26 repository paths; 0 missing: []. The first run reported the planned preview file `app/static/recording-preview/python-scheduler.html` as missing; it is a new file the plans introduce, so it was added to the checker's list of planned new files and the check was rerun. | Script output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| D-04 | NOT RUN | The PR head did not exist at this cutoff. | Wait for `Merge ready` on the final head and record its run URL and SHA in the PR before merging. |

## Findings, limitations and retests

No application behavior changed, so no application tests were run; required
CI applies the documentation scope gate. The plans describe future work; each
stage PR carries its own package and evidence.
