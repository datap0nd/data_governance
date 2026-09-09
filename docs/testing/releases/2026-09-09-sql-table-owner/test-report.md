# SQL table ownership: preview evidence

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-09, before production implementation and before PR.
- Tested revision: `8b4e342ad60b95043b8bef38566da1e34a515657`.
- Environment: Windows ARM64, Python 3.13.15, Playwright 1.62.0,
  Chrome 152.0.7977.76; desktop 1440x1000 and narrow 390x844 viewports.
- Overall finding: fictional preview checks PASS; actual ownership
  implementation, database integration, full regression and CI NOT RUN.

## Executed preview checks

An inline Playwright script, passed through PowerShell stdin to `python -X
utf8 -`, opened the local static preview and exercised these controls:

| Cases | Result | Observed evidence |
| --- | --- | --- |
| SO-01 | PASS | Changed Maya's SQL username to `maya_reports`; Users save succeeded and Flow Save did not alter the fictional database owner. |
| SO-02 | PASS | Run now changed the fictional owner from `metronome_loader` to `maya_reports` and reported 125 committed rows. |
| SO-03 | PASS | Missing role, insufficient permission and CSV-error scenarios preserved owner/rows and displayed recovery text; switching back to success completed. |
| SO-04 | PASS | Missing username, no owner and disabled SQL kept the fictional database owner unchanged; save and run feedback matched each case. |
| SO-05 | PASS | Owner selection survived navigation to Users and cancellation; narrow viewport had no page overflow; no browser JavaScript errors occurred. |

Screenshots are local fictional evidence under opaque package
`sql-owner-preview-evidence` (`users.png`, `success.png`, `failure.png`,
`narrow.png` in the local temporary directory). The Users, success and narrow
screenshots were visually inspected. These are preview assertions, not actual PostgreSQL tests.

## Unperformed checks

| Cases / check | Status | Reason / next step |
| --- | --- | --- |
| Owner feedback | PENDING | Preview opened and feedback requested under AGENTS.md before implementing a changed journey. |
| SO-06 through SO-12 | NOT RUN | Production implementation and test harness not yet written. |
| SO-13 work-PC/pgAdmin | NOT RUN | No live database operation performed. Requires deployed implementation and protected real-world evidence. |
| Full Python/frontend regression and CI | NOT RUN | No implementation revision exists yet. |
| Main merge | NOT MERGED | Preview-only work is local on `codex/sql-table-owner`; no PR has been created. |

Append later revision-specific evidence without erasing this preview cutoff.
Final CI/head evidence must be recorded in the PR testing section before merge.
