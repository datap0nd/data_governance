# Gemini reporting extension: test plan

- Scope: an optional Gemini CLI extension providing `/metronome` and
  `/html_replicate`. Metronome application, loader and scheduler code is unchanged.
- Baseline: `afb42a2d8be8d414d8dee5300967759003433027` (current main when started).
- Implementation revision: `f4140b85a8560fddd7e3e2847d2a626d3fec9829`.
- Results: [test-report.md](test-report.md).
- Environments: Windows ARM64/Node 24 locally; Node 22, Python 3.13 and disposable
  PostgreSQL 14/18 in CI. Browser checks use a fictional localhost preview.

## Prerequisites and fixtures

From the repository root run:

```powershell
npm.cmd ci --ignore-scripts --prefix integrations/metronome-gemini
```

The Node tests create synthetic API responses, temporary proposal receipts and
in-memory Korean workbooks. No Metronome service or database is needed locally.
The PostgreSQL case requires a disposable loopback database named exactly
`metronome_test_ownership`; CI provisions it. Never point this fixture at a
shared database: it creates/drops roles and a schema and revokes public CREATE.

The preview is explanatory and sends no requests to Metronome. Serve the
repository root so its existing fonts/style references resolve:

```powershell
python -m http.server 8768 --bind 127.0.0.1 --directory .
```

Open `http://127.0.0.1:8768/docs/previews/gemini-commands.html`.

## Cases

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| G-01 | Run `node --test integrations/metronome-gemini/test/transport.test.mjs`. | Real stdio MCP connects and lists tools; no credentials, approval tool, arbitrary API or shell tool; absent SQL configuration fails closed. Retain TAP/counts. |
| G-02 | Run `node --test integrations/metronome-gemini/test/read-tools.test.mjs`. | Out-of-scope flows/sites, remote API origins, scripts/email/refresh, multiple SQL statements, writes, role changes, unscoped relations and unsafe functions rejected. Catalog/recording/run projections omit synthetic secrets. Ambient upload/PG credentials are not used. Reader privilege failure prevents analysis. |
| G-03 | Run `node --test integrations/metronome-gemini/test/proposals.test.mjs`. | Preparing makes no API writes. Altered definitions and stale flows fail. Completed receipts survive restart and prevent duplicate dispatch. Uncertain responses and stale locks stop resubmission; corrupted state cannot silently reset. |
| G-04 | Run `node --test integrations/metronome-gemini/test/journey.test.mjs`. | Real MCP transport against a fictional HTTP API discovers source/schema, proposes without mutation, saves once, queues a run and reads status. Repeating a save returns the receipt. API mutation counters and redacted run output are asserted. This is not a Gemini model evaluation. |
| G-05 | Run `node --test integrations/metronome-gemini/test/workbook.test.mjs`. | ExcelJS round trip preserves Korean labels, merged headers, two tables, nulls, hidden rows, dates and formula/cache distinction. Manifest entry files exist; bundled browser omits evaluation/upload/network-dump tools. |
| G-06 | CI runs `node --test integrations/metronome-gemini/test/sql-postgres.mjs` with `METRONOME_TEST_PG_URL` set to the disposable DB. | Actual reader returns all three Korean fixture rows through aggregates: A=2 rows/200, B=1 row/null, September 1–3 coverage. Catalog exposes allowed columns. Direct reader INSERT is denied by PostgreSQL; a writable account is rejected before analysis. Total stays three. Both PG14 and PG18 required. |
| G-07 | In the fictional preview select Travel dates, then Change the period and Booking dates. | Both the mapping acknowledgement and proposed definition show the selected date meaning. Revised definition requires new review. Collect visible status text and revision. |
| G-08 | Preview: Decline, Approve this tool call, Simulate connection failure, then Check Metronome before retrying. | Decline preserves work; approval describes a disabled/unrun flow and next run review; failure explains uncertain outcome; recovery finds the existing fictional flow without a duplicate. These simulate Gemini interactions, not Metronome screens or a real Gemini confirmation. |
| G-09 | Parse installer, command/policy TOMLs and manifest; run skill validators and `node --check` on extension JS; inspect `git diff --check`; run `npm audit` in extension. | Valid syntax, literal arguments, both skills discoverable, ask-user write policies, no committed settings/secrets; dependency audit recorded. |
| G-10 | Required final-head Tests workflow. | Python regression, existing frontend contracts, extension tests, both PostgreSQL jobs and Merge ready pass. Record run URL and exact head in PR before merge. |

For a clean machine the full affected Node set is
`npm.cmd test --prefix integrations/metronome-gemini`. During development run
the smallest changed case; do not duplicate it with an overlapping suite unless
diagnosing a failure. CI uses `tests/test_gemini_extension.mjs` to include all
extension tests in the existing Node contract loop.

## Acceptance, limitations and cleanup

Owner feedback on the clickable fictional journey is required by DESIGN.md
before releasing it. No custom reporting UI is added to Metronome. Skills direct
Gemini to validate its own generated HTML filters, sorting, columns, groupings,
CSV encoding and totals against each report's mapped dataset. The automated
extension suite does not prove model-generated HTML quality or model compliance.

Application-wide agent credentials, atomic approval/version enforcement on
native schedules and schema freezing would require Metronome changes and are
outside this integration-only delivery. Confirmations use Gemini policy; direct
API/UI/shell operations and user/admin policy overrides are outside that boundary.
Fortnightly scheduling is not provided by the unchanged native scheduler.

Tests remove only their temporary receipt folders and dispose their named
database fixtures. Stop the preview server after review. To roll back the optional
extension use `gemini extensions uninstall metronome-gemini`; preserve reports and
receipts. Uninstalling does not delete or disable flows previously saved by users.
