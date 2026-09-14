# Gemini reporting extension: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-14 09:37 UTC, before final-head CI.
- Tested implementation: `f4140b85a8560fddd7e3e2847d2a626d3fec9829`. Local Node
  checks ran on its worktree contents before that commit; preview date-revision
  recovery was retested after the commit. Documentation commits do not change
  that implementation. Final tested head/run will be recorded in the PR.
- Environment: Windows ARM64; Node v24.19.0, npm 11.17.0; Python 3.13 ARM64 for
  syntax/skill checks; Codex in-app browser for the synthetic localhost preview.
- Finding at cutoff: affected local checks pass after the fixes below. CI and
  owner preview feedback remain pending. Metronome application files are unchanged.

## Executed checks

Commands below use `integrations/metronome-gemini` as cwd unless specified.

| Cases | Actual command/procedure | Results at cutoff |
| --- | --- | --- |
| G-01 | `node --test test/transport.test.mjs` | PASS, 1 test: actual MCP SDK client/stdin transport. Retested after server changes. |
| G-02 | `node --test test/read-tools.test.mjs`, followed by focused failed/changed-case retests | Initial run: 12 passed, 1 failed (join AST node). After repair, focused SQL tests passed. Final credential fix: `node --test --test-name-pattern='SQL\|reader\|actual pg' test/read-tools.test.mjs`, 6 passed, 0 failed, 0 skipped, 204 ms. All 14 distinct cases have passed; this is not a claim of a single 14-test final invocation. |
| G-03 | `node --test test/proposals.test.mjs` | PASS, 7 tests: binding, stale definitions, restart, uncertainty and locks. |
| G-04 | `node --test test/journey.test.mjs` | PASS, 1 test against synthetic HTTP server using real MCP transport. |
| G-05 | `node --test test/workbook.test.mjs` | PASS, 2 tests. Standard ExcelJS fixture, no custom workbook reader. |
| G-09 | `node --check` on client, sql, proposals, server and PostgreSQL fixture; PowerShell Parser on `install.ps1`; Python `tomllib`/`json` on commands, policy and manifest | PASS. No application Python changed. |
| G-09 | `python -X utf8 .../skill-creator/scripts/quick_validate.py integrations/metronome-gemini/skills/metronome` and corresponding `html-replicate` path from repo root | PASS, both skills valid. Validator is the local skill-creator utility, not a project runtime dependency. |
| G-09 | `npm audit --omit=dev`; `npm install --ignore-scripts --package-lock-only` after explicit connection-parser dependency | PASS, zero vulnerabilities; final lock audit covered 214 packages. |
| G-09 | `git diff --cached --check` before implementation commit | PASS. Dependency files and isolated extension only; no app/service/loader/scheduler code changes. |

There are 25 distinct affected local Node cases across these focused runs. CI
runs the whole Node collection on one final head. Model/API credentials are not
needed for these tests.

## Synthetic usability evidence

Preview: [fictional commands](../../../previews/gemini-commands.html), served at
localhost port 8768; CUA browser tab 2. This is a simulation of the intended
conversation and native tool confirmation, not a Gemini CLI screenshot.

| Case | Observed browser evidence on 2026-09-14 | Revision |
| --- | --- | --- |
| G-07 | Travel dates reveals definition; Change the period switches to booking dates and displays “Proposal changed. Review this new definition before approving.” Mapping acknowledgement and JSON both show booking dates. | `f4140b8`, 09:35 UTC |
| G-07 | Booking dates button changes the mapping to booking dates; screenshot inspected at the in-app browser's narrow viewport. Korean labels and wrapping JSON are readable with vertical scrolling. | Precommit preview; same layout in `f4140b8` |
| G-08 | Decline displayed “Nothing was submitted; your source mapping and proposal are preserved.” | Precommit preview; unchanged handler in `f4140b8` |
| G-08 | Approve displayed a disabled, unrun fictional Flow 42 and explicit next step to review/approve a run. | Precommit preview; unchanged handler in `f4140b8` |
| G-08 | Connection failure displayed “The flow may have been saved. No automatic retry will be sent.” Inspect action found matching fictional Flow 42 and removed retry prompt. | Precommit preview; unchanged handler in `f4140b8` |

The review caught an acknowledgement that retained the old date meaning after
revising the proposal. `render()` now updates both and clears stale status. The
changed path was retested against the committed revision. Owner acceptance is
pending at this cutoff; agent browser testing is not owner approval.

## Findings and limitations

- An initial test syntax error was repaired before the API test run. The first
  runnable read-tool suite then exposed missing join AST support; the allowlist
  was repaired and only affected SQL tests were rerun.
- Review caught node-postgres ambient password/port/TLS inheritance when a DSN
  omitted values. Explicit connection configuration and a password provider now
  prevent credential and `.pgpass` fallback. The regression test instantiates
  the real pg client with poisoned synthetic ambient values without connecting.
- The parser warns that `sslmode=require` semantics will change in a future
  major version. Current pinned versions retain certificate verification;
  prefer `sslmode=verify-full` in configuration. This warning is not a test skip.
- Dependency installation recovered from an initial registry connection reset.
  ExcelJS's uuid advisory was resolved with a pinned compatible override and
  verified with a conditional-formatting workbook round trip. Upstream package
  deprecation notices remain; npm audit reports no vulnerabilities at cutoff.
- Root `python tools/check.py setup` failed on Windows ARM64 because the pinned
  psycopg2-binary build requires missing `pg_config`. Evidence:
  `.test-runs/20260914T090829365Z-28272-3cb12209/result.json`. The lock was preserved.
  The affected code is Node; required final-head CI supplies the full Python
  regression on its supported environment. No local Python regression is claimed.
- Native Gemini discovery/confirmation and model-generated reports were not
  exercised here. Packaging uses Gemini's documented extension/skill/policy
  formats; automated tests exercise the actual MCP SDK and standard libraries.
  There is no custom HTML generator whose UI/CSV correctness can be asserted by
  this suite. Generated artifacts must be verified when Gemini creates them.
- Scope and SQL restrictions are enforced in this adapter; they do not introduce
  authentication to the unchanged application API or freeze loader-inferred
  schema. Native schedules and concurrent direct edits remain outside approval
  enforcement. See the extension README for the precise boundary.

## Pending evidence and merge

| Case | Status at cutoff | Next action |
| --- | --- | --- |
| G-06, G-10 | NOT RUN | Required CI on final head: Python/frontend/PostgreSQL 14/18/Merge ready. Record final run URL and head SHA in the PR testing section. |
| Owner review | Pending | Obtain feedback on the clickable fictional command/approval journey before release. |

This report stops before CI and merge. The PR testing section must retain the
final revision-specific results and owner feedback; its merge record supplies
the actual merge SHA. No branch or merge is described as deployed.
