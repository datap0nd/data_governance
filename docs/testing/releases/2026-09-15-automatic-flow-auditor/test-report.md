# Automatic Flow auditor: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff: **2026-09-15 14:56 UTC**. Final-head CI follows this committed report and is recorded in the PR Testing section before merge.
- Tested code revision: `e232430b5b31d171ba54d4b605c7570985fb1775`.
- Environment: Windows ARM64 host; checkout-owned official Python **3.13.7 x64**, exact `requirements-ci.lock`, pytest 9.1.1, Playwright 1.62.0, Chrome **152.0.7977.84**. The verifier launcher was Python 3.13.15 ARM64. No external model was used by the synthetic tests.
- Overall finding: the focused managed-reader, containment, lifecycle, API, AI-settings and actual browser checks passed. PostgreSQL runtime guarantees and full regression remain subject to final CI.

## Executed checks

Verifier run identifiers refer to `.test-runs/<identifier>/result.json` in the
task checkout. The committed [local verification record](evidence/local-verification.json)
preserves the exact final selection, environment, source fingerprint, revision,
duration and counts. Failed setup attempts remain in their original scratch
records and are not presented as passing tests.

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup prerequisite | `python tools/check.py verify ...` before refreshing the checkout environment | `e232430b`; stale lock marker | STOPPED before tests: verifier required a lock-exact environment; run `20260915T142817476Z-10092-1ba7e17e` | scratch result |
| Setup diagnosis | `python tools/check.py setup` | system Python 3.13.15 ARM64 | FAIL before tests: PyPI had no applicable ARM64 psycopg2 wheel and `pg_config` was absent; run `20260915T142826288Z-29644-39905b47` | scratch result |
| Setup x64 stale-environment diagnosis | `python tools/check.py setup --python .test-runs/python-bootstrap/x64/tools/python.exe` while the old `.venv` still existed | official Python 3.13.7 x64 bootstrap; old ARM64 `.venv` | FAIL before tests: setup reused the version-compatible but architecture-incompatible old environment; run `20260915T143111528Z-20592-a9052ffc` | scratch result |
| Setup recovery | Preserve the old `.venv`, then repeat `python tools/check.py setup --python .test-runs/python-bootstrap/x64/tools/python.exe` | official Python 3.13.7 x64, exact lock | PASS; clean checkout `.venv` created; run `20260915T143206781Z-27316-ab172b9a` | scratch result |
| A-01, A-03–A-07, U-01–U-02 and selected syntax | Exact command in the plan | `e232430b`; clean x64 environment; synthetic services/browser | **PASS: 68 passed, zero failures/errors/skips, two existing framework deprecation warnings; 107.56s pytest / 129.43s verifier** | run `20260915T145353386Z-4040-b2ade3c1`; [record](evidence/local-verification.json), [browser metadata](evidence/browser.json) |
| A-02 | `python -m pytest -q tests/test_check_command.py::test_windows_acl_fixture_denies_reader_style_write_and_recovers --tb=short` after correcting the disposable ACL target | same implementation; Windows ACL fixture | PASS: 1 passed, zero skips, 0.46s | terminal output in task history |
| A-08 collection | `python -m pytest -q tests/test_auditor_postgres.py --tb=short -ra` without a disposable DSN | local host | 14 skipped as designed; no PostgreSQL result claimed | dedicated final CI required |

The canonical run compiled every selected Python file and passed Node syntax for
`app/static/data_auditor.js`. The two warnings are the repository's existing
Starlette/httpx TestClient and AnyIO BlockingPortal deprecations; no dependency
was changed to suppress them.

## In-scope work after cutoff

| IDs | Status at report cutoff | Required next evidence |
| --- | --- | --- |
| A-08, R-01 | NOT RUN on the final PR head at this cutoff. | Required PostgreSQL 14/18 and final-head CI; record the final SHA, run URL and required job results in the PR before merge. |

## Usability evidence

The supplied implementation plan records the owner's approval of the revised
default-on journey with “Yes, implement.” The canonical synthetic browser pass
used the real page JavaScript, CSS and API against fictional reader/model data.
It exercised default On/all-Flows, failed and successful time saves, Run-now
outage/retry, Stop, Pause/Resume, navigation, partial coverage and escaped
evidence. No page errors occurred and the 390px page had no horizontal page
overflow.

Visual inspection at the tested revision confirmed that the failed edit remains
visible beside the action, inactive Stop is hidden, the schedule and next action
remain legible at 390px, coverage gaps are visible and script-like finding text
renders as text rather than markup.

| Evidence | Revision/source |
| --- | --- |
| [Save failure/recovery](evidence/save-recovery.png) | `e232430b`; canonical browser pass |
| [390px controls](evidence/narrow-controls.png) | `e232430b`; canonical browser pass |
| [Finding evidence](evidence/finding-evidence.png) | `e232430b`; canonical browser pass |
| [Browser metadata](evidence/browser.json) | `e232430b`; Chrome 152; JS SHA256 `2b3555ac617972c94ec0457da19fd6689b18e54871a4733f9891146fdfc1f5e0` |

Screenshots contain fictional fixture values only. Script-like text in the
evidence screenshot is an intentional escaping test.

## Findings, limitations and retests

- The first combined non-browser diagnostic had 12 expected failures while legacy tests still described operator keys, Flow selection and old settings. After updating them to the approved journey and correcting model-pin schedule preservation, the same selection passed 33 tests.
- The first browser diagnostic failed because its mocked catalog function did not accept the new frozen-config argument. The fixture was corrected without weakening application behavior; a later visual review found that global button styling overrode the HTML `hidden` attribute for inactive Stop. A scoped `[hidden]` rule and assertion were added; both browser cases passed canonically.
- A real Windows ACL diagnostic initially applied a recursive deny to its own directory and could not finish ACL enumeration. It was narrowed to the fixture file, then proved denied read/write and cleanup successfully.
- Synthetic model responses prove strict tool/schema/evidence validation, not real-model business accuracy. Unsupported formats, missing retained files, unsafe or mismatched SQL identities, append/concurrent targets, insufficient history and model outages remain explicit gaps.
- Application tool restrictions do not prove OS/network isolation of the separately deployed model server. The page and administrator guide state that remaining deployment responsibility.

## Post-cutoff retest evidence

At 2026-09-15 15:10 UTC, [final-head CI attempt 34985497104](https://github.com/datap0nd/data_governance/actions/runs/34985497104)
on `7fe56223454ed3a2a492781fa3687dc6889d0933` exposed one latent test-order
dependency in `test_postgres_dependency_scan_reports_each_database_phase`:
the test used the CI-wide SQLite path without creating its directory/database.
Shard 1 reported 1 failed, 1,078 passed, 37 skipped and 1,117 deselected;
PostgreSQL 14/18, Windows contracts and frontend checks passed in that attempt.
This attempt is retained as a failure and is not merge evidence.

The test now initializes and cleans up its own disposable database, matching the
neighboring scanner-observability tests. The exact failing selector passed on
committed revision `34d4139ed57490add66345450ebbae82007dd51b`:
1 passed, zero failures/errors/skips, 3.73 seconds pytest / 10.44 seconds verifier.
The [focused retest record](evidence/scanner-fixture-retest.json) preserves the
selection and source fingerprint. No application code changed in this
correction. Required CI must pass again on the later final head.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR Testing section must record the final head SHA, run URL, required job results
and merge. No deployment or post-merge application test is claimed here.
