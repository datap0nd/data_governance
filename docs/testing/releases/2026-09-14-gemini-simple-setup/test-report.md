# Gemini simple setup and database-wide reads: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-14 11:35 UTC, before owner feedback and CI.
- Baseline: `dd2a1747cd3da777706426042c5f6395878dde3d`; tests below ran on
  uncommitted follow-up contents in `codex/gemini-database-read-access`.
- Environment: Windows ARM64, Node 24.19.0, npm 11.17.0, PowerShell 7, Codex
  in-app browser with the localhost fictional preview.
- Finding at cutoff: affected local tests pass after the connection-field fix.
  Setup journey remains under owner review. No follow-up main merge is claimed.

## Executed checks

Commands use `integrations/metronome-gemini` unless stated otherwise.

| Cases | Actual command/procedure | Result |
| --- | --- | --- |
| S-01–03 | `node --test test/read-tools.test.mjs test/transport.test.mjs test/setup.test.mjs` | Initial: 20 passed, 1 failed, 0 skipped, 1,050 ms. The failure was punctuation in individual connection fields. Four setup tests passed, including seven isolated PowerShell journeys. |
| S-02 | `node --test --test-name-pattern='separate setup fields' test/read-tools.test.mjs` | PASS: 1 test, 0 failed/skipped, 173 ms after direct-field connection configuration replaced URL credential setters. |
| S-02 | `node --test --test-name-pattern='connection recovery' test/read-tools.test.mjs` | PASS: 1 new test, 0 failed/skipped, 168 ms; missing database and authentication failures never echo fixture secrets. |
| S-06 | `node --check` on `sql.mjs`, `server.mjs`, `settings.mjs`, `test/sql-postgres.mjs` | PASS. |
| S-06 | `npm.cmd install --ignore-scripts --package-lock-only` | PASS; 214 packages audited, zero vulnerabilities. |
| S-06 | `git diff --check` from root | PASS; Git emitted Windows LF/CRLF conversion notices. |

All 22 distinct local Node cases above have passed across those invocations;
this does not claim a single final 22-test run. The expanded PostgreSQL fixture
has only been syntax checked at this cutoff. The pinned connection parser still
emits its existing future-major `sslmode=require` semantics warning.

## Synthetic usability evidence

Preview: [Gemini setup](../../../previews/gemini-setup.html), CUA tab 3.
The exact tested file SHA256 is
`153f731975a80bf04824613e4ff66d0baccdc87d84cc3eaf0fff9bd441bce5c6`.
Observed on 2026-09-14 before this cutoff:

- Next moves server → username → password. Empty username shows inline feedback;
  entering `fictional_reader` continues. The password is a password input.
- Finish presents `/metronome` and `/html_replicate "folder X"` as next actions.
- Run setup again restores the completed server field. Interruption displays a
  recovery action; the unavailable-service example allows setup to continue.
- Clearing the server with the browser's field action then selecting Next
  displays “SQL skipped” with file/flow access retained. One initial automated
  empty-fill attempt did not clear the field; observing the field, clearing it
  explicitly and retrying verified the intended skip behavior.
- Screenshot inspected at the browser's narrow viewport: all three prompt
  descriptions and recovery controls fit with normal vertical scrolling.

This is a clickable fictional simulation, not a native Gemini CLI screenshot.
The PowerShell fixture exercises production setup orchestration with injected
CLI/service functions. Owner feedback was requested through the open preview;
agent testing is not owner acceptance.

## Pending in-scope evidence

| Cases | Status at cutoff | Next action |
| --- | --- | --- |
| S-05 | Owner feedback pending | Finish the changed setup journey after preview feedback. |
| S-06 | Remaining packaging checks pending | PowerShell Parser, skill and JSON checks before PR. |
| S-04, S-07 | CI pending | Run disposable PostgreSQL 14/18 and full regression on final PR head. |

## Merge evidence

No follow-up PR or merge at this cutoff. Before merging, the PR testing section
must link this package and record final CI run, tested head and owner feedback.
Later evidence must retain these initial results and their revision scope.

## Owner feedback and packaging update — 2026-09-14

The owner requested “merge to main” after the preview and review request. This
accepts the demonstrated setup journey; no further UI approval is pending.
Setup now also handles quoted whitespace as a skipped server and stops with a
clear message if the username is empty. These implement the demonstrated skip
and required-username behavior without adding prompts.

- `node --test --test-name-pattern='setup asks three' test/setup.test.mjs`:
  PASS, 1 test covering nine isolated PowerShell journeys, 0 failed/skipped,
  1,014 ms. This reruns the affected orchestration test after those changes.
- PowerShell Parser: PASS for installer, setup functions and fixture.
- JSON parse: PASS for package, lock and extension manifest.
- `python -X utf8 C:/Users/keeoh/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/metronome`:
  PASS, skill valid. This is a local validation utility, not a runtime dependency.
- Fetched `origin/main`: still baseline `dd2a1747cd3da777706426042c5f6395878dde3d`.

These results apply to the uncommitted contents included with this report update.
All local packaging checks are complete. Final-head CI, including disposable
PostgreSQL checks, remains pending and will be recorded in the PR before merge.
