# SQL table ownership and binary recordings: test report

Ownership/download code: `7574a0d43441eff3eef926fcb9590845ffd65b3a`.
Latest main integration: `c66a6fa4d964b4ea0da0194b29a59f28e7bd9b2b` (see final integration evidence below).
Owner approved the preview and requested implementation/merge. Focused tests,
production UI checks and all 13 PostgreSQL 18.6 transaction cases pass.
The full local suite completed with one stale test assertion; the correction
and 95 affected checks passed afterward. Final-head full CI is pending at this
writing cutoff; its results belong in the PR testing section. Live work-PC ASAP
and pgAdmin checks remain **NOT RUN**.

## Historical preview evidence (before implementation)

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-09, before production implementation and before PR.
- Tested revision: `8b4e342ad60b95043b8bef38566da1e34a515657`.
- Environment: Windows ARM64, Python 3.13.15, Playwright 1.62.0,
  Chrome 152.0.7977.76; desktop 1440x1000 and narrow 390x844 viewports.
- Overall finding: fictional preview checks PASS; actual ownership
  implementation, database integration, full regression and CI NOT RUN.

## Historical preview checks

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


## Implementation evidence — 2026-09-09

Owner approved the preview and explicitly requested implementation and merge.
Implementation revision: `90213ef512b45434c9fa368f6a6dec566afbcaac`.
This section supersedes the earlier pending statuses; the preview evidence
above is retained as a historical cutoff, not a current delivery status.

Environment: Windows ARM64, Python 3.13.15, Playwright 1.62.0,
Chrome 152.0.7977.76, Node 24.19.0. All fixtures use fictional data; no production
SQL or live portal was accessed.

| Check | Actual result at this cutoff | Evidence |
| --- | --- | --- |
| SQL owner API/job/worker tests | PASS: 13 tests in 6.71s. | `tests/test_sql_ownership.py`; includes ordinary runs and recording-validation worker claims, queued identity, pending owner selection, source/config hashes and committed outcome. |
| SQL and recorded storage focused tests | PASS: 77 tests in 22.04s. | `tests/test_flow_sql.py tests/test_recorded_output_storage.py`; raw bytes, suffix/checksum, true binary rejection for processing, UTF-16 LE/BE and prefix boundary. |
| Actual Chrome portable binary download | PASS: 1 test in 11.26s. | `tests/test_flow_recordings.py::test_portable_unchecked_recording_preserves_binary_browser_download`; generated standalone file runs in an isolated Python subprocess and saves exact protected-style fictional bytes as `.xlsx`, with no fabricated CSV. |
| Production Users / ownership log browser tests | PASS: 4 tests in 11.37s. | `tests/test_users_browser.py`; search, roles, validation, failed/pending save and recovery, cancel/delete, navigation, desktop/mobile, requested versus committed owner and HTML escaping. |
| Frontend | PASS: all 22 `tests/*.mjs` suites; 3 syntax checks. | Node invocation loop below; `app.js`, `users.js`, `flow_run_log.js`. |
| Full Python suite on `7574a0d4` | 1 failed, 1,886 passed, 13 skipped, 10 warnings in 813.87s. | The only failure hard-coded run-log asset `v=4`; production intentionally uses `v=5` to invalidate cache. Corrected the assertion to accept a versioned asset. No application code changed after this full run. |
| Final focused retest after correcting stale assertion | PASS: 95 tests in 39.30s. | Replication assertion plus ownership API/claims, SQL loader, recorded output storage and production Users/log browser module. Final full Windows/Linux CI required before merge. |
| Disposable PostgreSQL 18.6 transactions | PASS: 13 tests in 33.64s on `7574a0d43441eff3eef926fcb9590845ffd65b3a`. | Non-superuser loads, retained OID/grants/primary-key constraint/materialized view, repeated append/replace/schema expansion, missing role/INHERIT/SET/schema privileges/table ownership, real rollback after COPY and ownership change, punctuation role and unrelated table checks. PostgreSQL 14/18 CI pending. |
| Work-PC pgAdmin SO-13 / ASAP BD-04 | NOT RUN | Updated work-PC app/worker, real export and protected observations were unavailable in this implementation session. |
| Final-head CI / merge | PENDING | Final CI/head evidence belongs in the PR testing section after checks finish. |

Focused pytest commands used the following Windows-only wrapper because the
account cannot create pytest's cleanup symlink. This bypasses only that symlink;
it does not skip tests. CI uses ordinary `python -m pytest` unchanged:

```python
import _pytest.pathlib as p, pytest, tempfile, uuid
p._force_symlink = lambda *a, **k: None
raise SystemExit(pytest.main([
    # Insert the test modules/node ID listed in the result row above.
    "tests/test_sql_ownership.py", "-q",
    "--basetemp=" + tempfile.gettempdir() + "/sql-owner-unit-" + uuid.uuid4().hex,
]))
```

Frontend command (actual execution through `python -` on PowerShell):

```python
import pathlib, subprocess
for p in sorted(pathlib.Path("tests").glob("*.mjs")):
    subprocess.run(["node", str(p)], check=True)
for p in ["app/static/app.js", "app/static/users.js", "app/static/flow_run_log.js"]:
    subprocess.run(["node", "--check", p], check=True)
```

Fictional browser evidence is in local opaque package
`sql-owner-production-ui`: `users-desktop.png`, `users-mobile.png`,
`sql-owner-rollback.png`, `sql-owner-committed.png`. The desktop Users and
committed-owner screenshots were visually inspected; automated assertions
cover the other states. Source/control contents match the implementation
revision; the full suite repeats these browser checks on that revision.

Development failures were in new test setup, before the passing retests:
the pending-settings test referenced a nonexistent helper, then omitted a
required filename template; the run-log browser test initially reused the
Users JavaScript global scope. Corrected the fixtures and reran each affected
module. No failed result is represented as a passing attempt.

Limitations: PostgreSQL fixture results do not prove permissions on the work
PC. Opaque files are preserved only for download-only recordings; encryption
or proprietary formats are not decoded into rows for SQL. The raw `.xlsx`
suffix is preserved from the browser filename, not treated as proof of a
valid Excel workbook. Recognized corrupt workbooks and sign-in pages retain
their existing rejection. Legacy/recorded portable source includes shared SQL
logic; actual portable SQL integration is reported separately if run.


### Verification revision update

Final code revision for the full suite and PostgreSQL integration:
`7574a0d43441eff3eef926fcb9590845ffd65b3a`. Review added explicit
`no_parameters` execution for safely quoted SQL role identifiers containing
percent signs; the punctuation role fixture now includes `%(odd)s`.
The browser log fixture also resolves the production font assets from its
local static server. The initial full-suite attempt was stopped at 18%
without reported failures to restart against this fixed revision; it is not
counted as a completed run. No application code will be changed during the
replacement full-suite run.


### Actual PostgreSQL results

Executed `tests/test_sql_ownership_postgres.py -q -ra` against PostgreSQL 18.6
x64 (EDB Windows binaries, isolated loopback port 55438, disposable database
`metronome_test_ownership`). Python 3.13.15 x64, pytest 9.1.1,
SQLAlchemy 2.0.52, psycopg2-binary 2.9.12 and tzdata 2026.3.
All 13 cases passed in 33.64 seconds; no skips or warnings.
Evidence: local opaque `sql-owner-postgres-x64-output.log` and
`sql-owner-pg18-junit.xml`. The fixture cleans its uniquely named schema/roles.

The first attempt with ARM Python had 13 setup errors before any database
mutation because psycopg2 was unavailable in that interpreter. Retested using
an existing x64 interpreter with dependencies isolated under the temporary
`sql-owner-x64-test-deps` directory; application code was unchanged.

Equivalent local command (after configuring only the disposable DSN):

```python
# Run with Python 3.13.15 x64; the temporary dependencies are on sys.path.
import _pytest.pathlib as p, pytest, tempfile, uuid
p._force_symlink = lambda *a, **k: None
raise SystemExit(pytest.main([
    "tests/test_sql_ownership_postgres.py", "-q", "-ra",
    "--junitxml=" + tempfile.gettempdir() + "/sql-owner-pg18-junit.xml",
    "--basetemp=" + tempfile.gettempdir() + "/sql-owner-pg-" + uuid.uuid4().hex,
]))
```

Cleanup: catalog checks found **0** leftover fixture roles and **0** fixture schemas; the isolated PostgreSQL server was stopped after testing.


### Full local regression and corrected assertion

Full-suite invocation on `7574a0d43441eff3eef926fcb9590845ffd65b3a`:

```python
import _pytest.pathlib as p, pytest, tempfile, uuid, pathlib
p._force_symlink = lambda *a, **k: None
root = pathlib.Path(tempfile.gettempdir()) / "sql-owner-full"
root.mkdir(exist_ok=True)
raise SystemExit(pytest.main([
    "tests", "-q", "-ra", "--junitxml=" + str(root / "junit.xml"),
    "--basetemp=" + tempfile.gettempdir() + "/sql-owner-full-" + uuid.uuid4().hex,
]))
```

Actual result: **1 failed, 1,886 passed, 13 skipped, 10 warnings in 813.87s**.
The failure was `test_flow_builder_can_replicate_an_existing_flow`, whose
unrelated run-log script assertion required cache version 4. The new UI uses
version 5. The corrected assertion checks that a versioned run-log script is
loaded; the replication behavior assertions remain unchanged.

Retest command: the same Windows wrapper with
`tests/test_flows.py::test_flow_builder_can_replicate_an_existing_flow`,
`tests/test_sql_ownership.py`, `tests/test_flow_sql.py`,
`tests/test_recorded_output_storage.py`, `tests/test_users_browser.py`, `-q`.
**95 passed in 39.30s**, with test contents committed as
`d8ee834b702616fa1f29b89ea73c4245ab59700e`. The only change after the full-suite code revision
is this test assertion and release documentation. Final-head CI reruns the
entire suite on Windows and Linux before merge.

All 13 local full-suite skips were the explicit disposable PostgreSQL fixture,
which passed separately in the x64 PostgreSQL run above. The 10 warnings were
existing Starlette/httpx TestClient deprecations (one import warning and nine
request-timeout warnings in parallel download tests). None were suppressed.
Evidence packages: `sql-owner-full/junit.xml`,
`sql-owner-full-final-output.log`, and `sql-owner-production-ui` for the repeated
browser walkthrough. Relative documentation links were checked: 3 documents,
54 links, all resolve. `git diff --check` passed.

Final CI/merge evidence is intentionally outside this committed report's
cutoff and must be completed in the PR testing section. SO-13 and BD-04 remain
**NOT RUN**; portable SQL against an external/live database is **NOT RUN**.


### Latest main integration — 2026-09-09

While this PR was being prepared, PR #89 merged as
`cc9ddacb92e2246b13e60ef90cb83b81c790ea37`. Integrated that current `main`
without discarding its save-without-testing behavior. The only textual
conflict was the testing index; both release entries were retained.
Combined tested revision: `c66a6fa4d964b4ea0da0194b29a59f28e7bd9b2b`.

Ran the Windows pytest wrapper with `tests/test_sql_ownership.py`,
`tests/test_recording_journey.py`, `tests/test_recording_visual_editor.py`,
`tests/test_recorded_output_storage.py`, `tests/test_users_browser.py`, `-q`.
**52 passed, 1 existing Starlette/httpx deprecation warning in 135.43s**.
All **23** Node suites and **4** syntax checks (`app.js`, `users.js`,
`flow_run_log.js`, `flow_recording_editor.js`) also passed. No unresolved
conflicts remain. Final-head full CI on the combined code is required and
will be recorded in PR #90 before merging.
