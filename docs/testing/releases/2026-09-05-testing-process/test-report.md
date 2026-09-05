# Testing documentation process: test report

- Plan: [test-plan.md](test-plan.md).
- Change and final merge evidence: [PR #70](https://github.com/datap0nd/data_governance/pull/70).
- Evidence date: 2026-09-05 UTC.
- Application/source baseline: `bfff7bd0aa3af4a21f02706db65bc3b3e0524694`.
- Scope: documentation changes in the working tree based on that commit; no
  application or CI code changes. The final documentation commit is identified
  by the PR linked below, not by a predicted self-referential SHA.

## Results at document cutoff

| Case | Result | Evidence |
| --- | --- | --- |
| DOC-01 | PASS | Python standard-library link audit resolved all 47 relative file links in the new instructions/templates/release docs and added entry points. Markdown code fences balanced. |
| DOC-02 | PASS | Python csv/re audit: 40 unique plan IDs exactly match 40 worksheet rows, all NOT RUN, with 13 consistently parsed columns. |
| DOC-03 | PASS | GitHub PR #69 and final CI metadata/log summaries re-read on 2026-09-05; retained local log summary checked. Exact evidence is in the Flows report. |
| DOC-04 | PASS | Reviewed root instructions, PR template, index and templates together: proportional per-merge documentation, separate live status, final-head evidence recorded in PR, no claim of an automatic documentation gate. |
| DOC-05 | PASS | git diff --check passed; changed-path and content review found documentation only. Case labels/ranges checked against feature docs, recording editor and relevant test definitions. |
| DOC-06 | NOT RUN | Requires the final push, CI and main merge. |

Local commands were a PowerShell here-string piped to `python -` (stdlib
`pathlib`/`urllib.parse`/`re` to resolve relative file targets, `csv.DictReader`
to verify the 40-case/13-column worksheet), `git diff --check`,
`git diff --stat` and `git status --short`. Evidence attribution used
`gh pr view 69 --json headRefOid,mergeCommit,mergedAt,body`,
`gh run view 33974422732 --json status,conclusion,headSha,jobs` and
`gh run view 33974422732 --log` with summary filtering. These are documentation
checks, not a new live application test. Git emitted ordinary Windows LF/CRLF
conversion notices; no whitespace errors were reported.

## Merge evidence

The PR testing section will record the actual final documentation head, CI run,
result and GitHub publication checks before merge. That evidence occurs after
this report's committed cutoff. Historical Flows checks are documented in the
[Flows report](../2026-09-05-flows/test-report.md), not represented as a new
application test run for this documentation change. Live work-PC cases remain
NOT RUN in the Flows worksheet.
