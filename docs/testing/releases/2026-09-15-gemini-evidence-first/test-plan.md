# Gemini evidence-first flow authoring: test plan

- Baseline: `b39c8193a8e378f3c211f8137297c18568b684f2`, current `origin/main` when work began.
- Scope: strengthen the external Gemini extension; preserve Metronome application logic.
- Report: [test-report.md](test-report.md).
- Review: [fictional walkthrough](../../../previews/gemini-evidence-first.html).
- Owner approved the demonstrated recorder fallback on 2026-09-15: use available control; pause if it fails.

## Fixtures and cases

Use fictional Korean .xlsx/.csv references and isolated HTTP/PostgreSQL fixtures.
Never use the operator's application database or credentials in these tests.
Prerequisites: Node 22+, locked dependencies installed with `npm ci --ignore-scripts`,
and Edge on Windows or `npx playwright install chromium` on Linux. The browser
fixture runs headless against localhost fictional pages. Commands below use
`integrations/metronome-gemini` as cwd.

| ID | Actions | Expected evidence |
| --- | --- | --- |
| E-01 | `node --test test/verification.test.mjs` | Read actual XLSX/CSV bytes. Preserve duplicate multiplicities, blanks and leading-zero IDs. Require explicit worksheet/table bounds and unique headers. Missing formula caches and incomplete SQL scans cannot produce a match. |
| E-02 | Walk the fictional preview: interrupted download → retry → compare → approve setup → missing recorder control → resume capture → approve run → match/difference/failure outcomes. | No flow proposal before independent comparison. No detected-controls fallback. Queued/failed runs are incomplete; successful execution with differing data remains unreconciled. Owner reviews the recorder-window handoff. |
| E-03 | `node --test test/journey.test.mjs`: real browser/stdio MCP/HTTP fixture: reference → captured independent browser download → comparison → recorded draft/session → approved definition/run → terminal status → actual file and SQL verification. | Actual artifact evidence is required; source/flow/revision/run identities match; duplicate requests do not duplicate operations. Model-supplied success flags cannot substitute for evidence. |
| E-04 | `node --test test/proposals.test.mjs test/read-tools.test.mjs`, plus the journey case: unrelated recording/session, changed reference/output, wrong run/SQL target, missing output, incomplete scans, unsupported recorder control, direct catalog-mode proposals and failed/uncertain requests. | Fail closed with one concrete next action; preserve previous evidence; never mutate application SQLite or claim completion. |
| E-05 | Check all extension/test `.mjs` with `node --check`; parse command/policy TOML and manifests; run setup/workbook/transport contracts. Final-head CI runs `node tests/test_gemini_extension.mjs`, full Python shards, Windows contracts and PostgreSQL 14/18 fixtures. | Required regression and Merge ready pass; PR records final tested SHA and CI run before merge. |

## Acceptance and cleanup

Owner feedback was obtained before workflow implementation: Gemini uses its
available computer-control tool on the native recorder, pausing if control fails.
The fictional preview demonstrates this fallback. The automated journey tests
real independent browser downloads and synthetic recorder API responses; it does
not operate a native Metronome recorder or evaluate the model's behavior.

For E-03, the fixture first omits evidence, submits a supplied file as a portal
download, and tries catalog mode/wrong sources: all must fail before a save. It
captures a real download receipt, creates/finishes a synthetic recorded draft,
reviews the SQL target, queues once and checks status. Then it introduces wrong
recordings, changed reference/output bytes, wrong SQL target/server, missing
subsidiaries and truncated SQL. No case may return SUCCESSFUL until the actual
output and SQL rows agree with the correct sample. Inspect private difference
JSON for missing/extra rows; do not infer refresh. The interrupted-download case
must fail or disconnect, then navigate/download again after browser restart
without resubmitting a Metronome mutation.

PostgreSQL CI uses only the explicit disposable `metronome_test_ownership`
database. Its reader fixture verifies full table snapshots, read privileges,
rejection of private/writable targets, and a 50,001-row scan that cannot certify
completeness. SQL analysis never uses the fixture's administrative setup client.

Retain test stdout, final tested SHA/CI link and the preview hash. No business
rows or credentials belong in public evidence. Upgrade using the existing
extension installer from main and restart Gemini; rollback to the preceding
extension revision through that installer. No application migration is needed.
Keep proposal/evidence stores during upgrade or rollback; never clear receipts
to bypass uncertain-request protection.

Fixtures delete only their generated temporary directories. Stop the fictional
preview server after review. Keep evidence outside the application checkout.
The final PR must include this plan/report and final-head CI evidence. No live
system results are implied by synthetic checks.
