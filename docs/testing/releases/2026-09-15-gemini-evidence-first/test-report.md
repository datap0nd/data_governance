# Gemini evidence-first flow authoring: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-15 07:03 UTC.
- Baseline: `b39c8193a8e378f3c211f8137297c18568b684f2`; checks ran on uncommitted
  additions in `codex/gemini-evidence-first`.
- Environment: Windows ARM64, Node 24.19.0, existing locked ExcelJS, Codex
  in-app browser serving a fictional localhost preview.
- State: initial verifier implemented and tested; MCP workflow wiring,
  recording tools and authoring instructions are not yet implemented.
  Owner feedback on the material journey is pending. No PR/merge yet.

## Executed evidence

- `node --test test/verification.test.mjs`: **3 passed, 0 failed, 0 skipped**,
  765 ms. Cases cover actual Korean XLSX/CSV values, leading-zero IDs, duplicate
  multiplicities, null/blank distinctions, table bounds, duplicated headings,
  missing/cached formula results, full-table SQL construction and rejected
  truncated scans. Formula comparisons explicitly do not recalculate cells.
- `node --check integrations/metronome-gemini/verification.mjs` and corresponding
  `sql.mjs` from repository root: PASS.
- An initial patch targeted the verifier at the repository root; it was moved
  into the extension before executing tests. No application files changed.

## Synthetic browser walkthrough

Preview SHA256:
`be1d93d2b54ba0a05918a15c13d895101ce59216f7cb76b81ce9e3fefbc3fd10`.
CUA tab 6, 2026-09-15, before the cutoff:

- Interrupted download hid the proposal and displayed retry guidance.
- Independent download revealed the comparison and reviewable source/SQL target.
- Approve setup revealed the Playwright recording step. Unavailable control
  displayed BLOCKED, preserved the draft and prohibited detected-control fallback.
- Saved recording revealed run confirmation. Queued status explicitly said not
  complete. Verified match showed successful execution plus Excel and SQL counts.
- Difference result remained DATA DIFFERENCES with 120 reference rows, 122 actual,
  1 missing and 3 extra. Refresh was not assumed. Failed run remained incomplete.
- Screenshot inspected: status, next controls and evidence summaries were legible
  and wrapped without horizontal clipping.

The original preview tab was no longer present; QA continued in a new background
tab. These are fictional UI states, not evidence of Gemini or a portal run.

## Pending in-scope work

Owner feedback, workflow integration and its negative/recovery fixtures,
recording API controls, updated Gemini instructions, packaging checks and required
final-head CI remain pending. The verifier currently caps scans at 50,000 rows;
file/value limits and the reader's existing SQL byte/time limits remain enforced.
Hitting a limit must remain incomplete, never a claimed full match.

Before merge, append revision-specific evidence and record the final CI run/head
in the PR. Preserve this initial cutoff and do not imply unperformed checks passed.


## 2026-09-15 implementation evidence (cutoff 07:35 UTC)

This dated entry supersedes the initial pending-implementation status above.
Owner feedback: “gemini can control, I had it do it before. but yes if it cant,
pause.” The demonstrated recorder fallback is approved. Preview bytes remain
unchanged from the hash and browser walkthrough recorded above.

Implemented extension version 0.2.0: mandatory reference/download evidence,
recorded-only portal proposals, scoped recording API actions, immutable proposal
and recording fingerprints, actual run/output/SQL reconciliation, concise skills
and repository entry guidance prohibiting application SQLite access. Application
logic is unchanged. The browser adapter uses Microsoft's existing operations,
an isolated context and real download events. Source data stays in private local
evidence, not these docs.

Local environment: Windows ARM64, Node 24.19.0, headless Edge, locked packages.
Revision: `codex/gemini-evidence-first` implementation worktree based on
`b39c8193a8e378f3c211f8137297c18568b684f2`; all results below precede the first
implementation commit. Final committed head and CI evidence are recorded in the
PR before merge. Current main advanced to `043e55ca43cd69a4879485434bfa02e09c5e626f`
during development; integration will preserve that unrelated release.

Executed one affected extension set, then only changed/failed subsets:

- Initial `node --test integrations/metronome-gemini/test/*.test.mjs`:
  setup/workbook cases passed; other modules failed to load due to a missing
  closing parenthesis. Fixed before subsequent checks.
- `node --test` of journey/proposals/read-tools/transport/verification:
  28 passed, 1 journey failed. Unit/stdio/SQL denial tests passed. The journey
  exposed the official MCP context-getter constraint and current tool schemas;
  corrected isolated-context configuration and used the discovered snapshot and
  click target contract. Only the failed journey was repeated while diagnosing.
- Final `node --test integrations/metronome-gemini/test/journey.test.mjs`:
  **1 passed**, 0 failed/skipped, 10.53 s (9.65 s test). Real browser/stdio MCP
  download, synthetic recording API, actual CSV comparisons, synthetic SQL,
  scope/revision/target/server rejection, altered artifacts, duplicate dispatch,
  missing subsidiary, incomplete scans and browser restart recovery passed.
- Changed workbook/manifest contracts: **2 passed**. The fixture confirms the
  browser adapter entrypoint and isolated new context; Korean workbook structure
  remains intact.
- `node --test --test-name-pattern="SQL is opt-in|verification binds"
  integrations/metronome-gemini/test/read-tools.test.mjs`: **2 passed**, including
  snapshot no-fallback behavior and exact reader host/port identity.
- Final `node --test integrations/metronome-gemini/test/verification.test.mjs`:
  **3 passed**, 0 failed/skipped. Includes appended XLSX rows through open-ended
  ranges, duplicates, blanks, formulas, column mismatches and truncated SQL.
- All extension/test `.mjs` passed `node --check`; JSON manifests and command/
  policy TOML parsed successfully. `git diff --check` passed; only expected
  Windows LF/CRLF conversion notices.

Warnings/limits: the existing pg connection-string TLS-alias warning remains;
certificate verification was not relaxed. The pinned Microsoft MCP has an
unhandled `saveAs` rejection on an interrupted transfer. The synthetic browser
runs in its normal separate stdio process, and restart/retry is verified; no
partial file obtains a valid completed evidence receipt. A browser disconnect
never retries a flow mutation. This release does not patch the upstream library.

The native recorder response is synthetic, and Gemini model behavior is not
measured by these deterministic tests. Full comparison means the explicitly
declared tables and all reader-visible SQL rows within documented limits. There
is no arbitrary transformation/mapping engine, automatic refresh explanation,
HTML feature, schema-migration freeze or application-wide agent authorization.
The owner-supplied reference remains authoritative; unresolved mappings/differences
must stay explicit. Unrestricted shell, direct API/UI and native schedules remain
outside MCP enforcement.

At this cutoff, final-head CI and merge are pending. PostgreSQL 14/18 and full
Python regression run in required CI; no duplicate full local suite was run.
The PR will retain the final tested head, run links and results before merge.


### Rebase integration

Rebased onto `043e55ca43cd69a4879485434bfa02e09c5e626f` before opening the PR.
The sole conflict was the testing README's release table; both releases were
preserved. No extension or test code changed in the rebase, so the affected
local tests were not duplicated. Final-head CI tests the integrated revision.
