# Read-only data auditor: test plan

- Change: on-demand and optional overnight data inspection with Qwen3.8-27B, restricted reads and evidence-backed in-app alerts.
- Implementation revision after integration with main: `6e291c264df6f2cf81a2a011ba5febd2b38cf25e` (base `043e55ca43cd69a4879485434bfa02e09c5e626f`). The PR records its final tested head.
- Related [report](test-report.md) and [administrator guide](../../../data_auditor.md).
- Environments: isolated Windows Python fixtures and synthetic Chrome browser; final-head Ubuntu regression, Windows verifier contracts, and disposable PostgreSQL 14/18 in CI.

## Prerequisites and test data

Use the checkout-owned Python 3.13 `.venv` and `requirements-ci.lock` through
`python tools/check.py setup`. Install Chrome for the synthetic UI fixture with
`.venv/Scripts/python -m playwright install chrome` on Windows (use
`.venv/bin/python` on Linux). The verifier isolates all application state.
No configured model or reader service is needed for local synthetic tests.

Fixtures create fictional flows, normalized CSVs, a separate sanitized SQLite
manifest and deterministic model responses. Historical observations use four
different Dubai dates on the same weekday with 100,000 rows; the current run
has 70,000. Never substitute actual application databases or exports.

The PostgreSQL job uses `METRONOME_TEST_POSTGRES_DSN` pointing to the workflow's
disposable loopback `metronome_test_*` database. Tests create their own database
and restricted roles and remove those resources afterwards. A missing DSN
skips these cases in the general Python jobs; the dedicated PG jobs must run
them successfully on both supported versions.

Operational setup is deliberately disabled until the administrator supplies
the isolated reader, policy, separate credentials and model endpoint described
in the guide. Qwen has no mutation tools. Trusted Metronome code retains only
its own manifests, profiles, history and validated alerts.

## Cases and observable results

| ID | Actions / test selector | Expected result and evidence |
| --- | --- | --- |
| A01 | `tests/test_data_auditor.py`: recount CSV with embedded newlines and inaccurate manifest row metadata; inspect exact bytes before/after. | Count comes from complete parsed bytes, numeric totals match, file unchanged, raw category labels absent. |
| A02 | Same suite: changed checksum, outside root, hard link, ragged row, cancellation, byte limit and missing approved column. | Inspection fails closed; no complete profile or clean verdict. |
| A03 | Same suite: initialize stable 100,000-row history, inspect 70,000 rows twice, resolve its alert, inspect again. | A computed -30% finding with evidence; no duplicate alert, no automated resolution/reopening and no baseline adaptation to bad data. Volatile or changed-scope history abstains. |
| A04 | Same suite: SQL/download count difference with and without a proven same-run boundary. | Insertion mismatch only for the proven boundary; append/unknown boundary is unverified. |
| A05 | Same suite: absent/wrong operator token, cross-origin, remote HTTP, matching HTTPS origin under a URL prefix, extra settings fields, reader outage. | Reject unauthorized controls, remote cleartext and arbitrary endpoint settings. Keep settings on save failure; disabling still works during outage. |
| A06 | Same suite: hostile model requests a write tool, extra SQL, out-of-scope run, fabricated finding and instruction-like output. | Rejected output; protected files and flow configuration unchanged. Only fixed `read_profile` tool is exported. Pending transport cancels and late profiles/findings cannot persist. |
| A07 | `tests/test_auditor_manifest.py`: publish a synthetic job containing unrelated secret fields, inspect through actual reader API, attempt to use main SQLite, compare reporting windows. | Reader sees separate sanitized manifest; main database unchanged/refused; completed windows compare only under explicit policy, changed filters/partial windows remain distinct. |
| A08 | `tests/test_auditor_lifecycle.py`: run a complete two-turn native-tool exchange, overnight trigger twice, manual run with overnight off, long outage, restart/stop and coverage alerts. | Computed alert, unchanged flow, one scheduled claim at Dubai time, manual start allowed, no backlog, interrupted work visible, incomplete coverage produces a deduplicated notice. |
| A09 | Lifecycle suite: execute fictional Python transforms which stay stable or change their own script. | Existing transformation still completes; only the stable script gets a provenance checksum. No quality gate is introduced into Flows. |
| A10 | `tests/test_auditor_postgres.py`: inspect the disposable approved table; grant column UPDATE, PUBLIC TEMP, schema CREATE, membership, user-function execution, sequence use and ownership one at a time. | Correct aggregate values, unchanged table; every unsafe privilege combination is rejected before profiling. |
| A11 | Same PG suite: column/RLS/view/domain/expression-index drift and explicit DELETE inside the reader transaction. | Unapproved or indirect-execution definitions rejected, transaction read-only, DELETE denied, rollback leaves rows unchanged. |
| U01 | `tests/test_auditor_browser.py::test_enable_schedule_save_failure_run_stop_disable_and_navigation`: unlock, enable, select flows, set overnight 03:15 Dubai, remove all selections, save, inject save failure, retry, inject reader outage, reload, run twice, stop, disable, navigate away/back, use 390px viewport. | Clear selection requirement, adjacent feedback, preserved selections after failure/navigation, one active run, visible stop/disable result, no horizontal overflow and no key in local/session storage. Capture save-recovery and narrow screenshots. |
| U02 | `tests/test_auditor_browser.py::test_partial_coverage_and_evidence_are_visible_and_escaped`: show partial coverage, open evidence containing script-like text, return. | Gaps are visible; evidence is text, never executable markup; enable state matches saved settings; dialog and back navigation work. Capture evidence screenshot and browser/source metadata. |
| R01 | Required final-head CI. | Complete non-overlapping Python shard union, frontend contracts/syntax, Windows verifier contracts, PG14/18 and `Merge ready` all pass. Record run URL and final SHA in PR. |
| R02 | Synthetic `tests/test_recording_ranges.py` virtualized scrolling (immediate/deferred repaint), ordinary range with a disabled future week, duplicate/missing-week rejection and portable contract. | Scroll handlers and a rendered frame finish before week identities are read; all six expected weeks selected, future week skipped, invalid ranges still rejected. Existing user controls are unchanged. |
| R03 | `python tools/check.py verify --test tests/test_scan_status_consumers.py --syntax tests/test_scan_status_consumers.py`; the redaction case starts with an unavailable process-default settings path. | Scanner consumers use the same isolated database for job state and notification settings; all redaction/lifecycle assertions pass without relying on earlier tests to create a different database. |

## Commands

The report records the actual incremental selections and corrections. For a
fresh verification, run this affected set once; do not also run a local full
suite containing the same cases:

```text
python tools/check.py verify --test tests/test_data_auditor.py --test tests/test_auditor_manifest.py --test tests/test_auditor_lifecycle.py --test tests/test_auditor_browser.py --syntax app/main.py --syntax app/flow_worker.py --syntax app/auditor/router.py --syntax app/auditor/engine.py --syntax app/auditor/store.py --syntax app/auditor/config.py --syntax app/auditor/manifest.py --syntax app/auditor/detection.py --syntax auditor_reader/policy.py --syntax auditor_reader/profiles.py --syntax auditor_reader/postgres.py --syntax auditor_reader/service.py --syntax auditor_reader/__main__.py --syntax app/static/data_auditor.js --syntax app/static/app.js
```

CI runs the PG cases using its disposable database:

```text
python -m pytest tests/test_sql_ownership_postgres.py tests/test_auditor_postgres.py -q -ra
```

Set `AUDITOR_EVIDENCE_DIR` to a task-owned scratch directory to retain browser
screenshots and browser/source metadata. Otherwise these remain disposable.

## Usability review

The clickable fictional preview is
`app/static/recording-preview/data-auditor.html`. Serve it with
`python -m http.server 8766 --bind 127.0.0.1 --directory app` and open
`http://127.0.0.1:8766/static/recording-preview/data-auditor.html`.
Walk enable/select/schedule/save/run/stop/disable/evidence and its simulated
save and reader failures. The owner instructed **“keep going”** with this preview
available; this was treated as authorization for the demonstrated controls.
This does not claim the owner personally walked each control. Actual UI/API
verification uses U01/U02 rather than the preview's simulated implementation.

## Acceptance, cleanup and rollback

Accept after the above automated boundaries, synthetic UI and final CI pass.
Capture revision, source fingerprint, counts, warnings and actual screenshots;
do not interpret unverified coverage as clean data or synthetic model responses
as a real-weight accuracy benchmark.

Fixture servers, browser contexts and temporary state close at teardown. Retain
only designated synthetic evidence. Stop the task's static preview server when
finished. If installed, disabling the auditor stops current work and cancels
future overnight dispatch; it does not alter Flows. Remove its service-only
credentials to prevent later activation. Reverting the feature leaves its
additive audit tables inert; never delete application data during rollback.
