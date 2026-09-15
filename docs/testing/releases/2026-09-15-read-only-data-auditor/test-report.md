# Read-only data auditor: test report

- [Test plan](test-plan.md) · [Administrator guide](../../../data_auditor.md).
- Evidence cutoff: **2026-09-15 08:01 UTC**. Final-head CI follows this committed report and is recorded in the PR testing section before merge.
- Local tests ran against base `b39c8193a8e378f3c211f8137297c18568b684f2` plus the then-uncommitted implementation. Individual source fingerprints, selectors, timings and counts are preserved in [local-verification.json](evidence/local-verification.json).
- Implementation committed, then rebased without conflict onto `043e55ca43cd69a4879485434bfa02e09c5e626f`, producing `6e291c264df6f2cf81a2a011ba5febd2b38cf25e`. Auditor application and test files were byte-identical in Git across that rebase; no duplicated application run was justified by it.
- Environment: Windows ARM host; checkout-owned **Python 3.13.7 x64**, exact `requirements-ci.lock`, pytest 9.1.1, Playwright 1.62.0, Chrome **152.0.7977.84**. The verifier launcher was Python 3.13.15. No external model was used by the synthetic tests.
- Finding at cutoff: focused behavior, cancellation, containment and actual UI/API tests passed after the corrections below. PostgreSQL runtime guarantees and full regression remain subject to final CI.

## Executed checks

All run identifiers refer to `.test-runs/<identifier>/result.json` in the task
checkout. The linked JSON preserves exact test/syntax selections and a copyable
command for every verifier invocation. Counts below are separate attempts;
overlapping correction runs must not be added into a unique coverage total.

| Cases | Run identifier | Result |
| --- | --- | --- |
| Setup | `20260915T071551716Z-27652-19d0cbc1` | FAIL: system ARM Python could not build the locked psycopg2 package (`pg_config` absent). |
| Setup recovery | `20260915T071957894Z-17332-0d2563c1` | PASS: checkout `.venv` rebuilt with official Python 3.13.7 x64 and locked dependencies. |
| Syntax invocation | `20260915T072947969Z-7756-24445b36` | FAIL: verifier accepts files, not directory syntax selectors; no application test result claimed. |
| A01–A06 collection | `20260915T072957868Z-20012-a3e0b810` | FAIL: Windows Application Control prevented psycopg2 DLL loading during collection. |
| A01–A06 first execution | `20260915T073019309Z-26996-68df2e23` | FAIL overall: 21 test cases completed, but pytest temporary-directory symlink cleanup exited with an error. This is not reported as a passing invocation. |
| A01–A06 canonical-path diagnosis | `20260915T073136282Z-22188-d04526f5` | FAIL: 20 passed, one failed; the Windows virtualized fixture path did not match its real handle path. |
| A01–A02 correction | `20260915T073217677Z-21928-214b7ccb` | PASS: 8 passed, zero skips; CSV/protected-path syntax and behavior. |
| A05–A07 correction and new coverage | `20260915T073911803Z-24824-cf027343` | PASS: 9 passed, zero skips; separate manifest, hostile model and operator/outage behavior. |
| U01–U02 | `20260915T075007297Z-16604-7f6a40ef` | PASS: 2 passed, zero skips; 32.73 seconds pytest time, actual UI/API with synthetic reader/executor. |
| A03–A04, A08–A09, U02 correction | `20260915T075303414Z-23412-2980d938` | PASS: 11 passed, zero skips; 11.24 seconds pytest time; native model loop, lifecycle, provenance, baseline dedup and evidence state. |
| A05 HTTPS prefix correction | `20260915T075852014Z-23676-4fb3d702` | PASS: 1 passed, zero skips; 1.22 seconds pytest time; same-origin controls work under a URL prefix, remote HTTP remains rejected. Main/router syntax passed. |

Successful pytest invocations emitted the existing Starlette/httpx TestClient
and AnyIO BlockingPortal deprecation warnings (two warnings per invocation).
No dependency was changed to suppress them. Syntax and `git diff --check`
completed successfully for the changed sources; CRLF normalization notices are
Git platform notices, not failed checks.

Exact successful setup command:

```text
python tools/check.py setup --python .test-runs/python-bootstrap/x64/tools/python.exe
```

The official x64 runtime was downloaded from the Python NuGet package into
task-owned scratch storage. The failed ARM environment was retained in scratch
for diagnosis. Local OS policy was not bypassed: the reader's SQL driver is now
imported only when SQL inspection actually runs. PG behavior is exercised in the
dedicated CI services below.

## In-scope work after cutoff

| Cases | Status at report cutoff | Required next evidence |
| --- | --- | --- |
| A10–A11 | NOT RUN locally; SQL driver blocked by this host's Application Control. | Dedicated PostgreSQL 14/18 CI with the disposable service DSN; no SQL test skips accepted in those jobs. |
| R01 | NOT RUN on the final PR head at this cutoff. | Required `Merge ready`, full Python shard audit, frontend and Windows contracts; record run URL and head SHA in PR before merge. |

## Usability evidence

The owner instructed “keep going” while the clickable fictional preview was
available. It was treated as approval to implement the demonstrated controls;
the record does not assert that the owner reviewed every state personally.

The synthetic browser walkthrough exercised every changed control, wrong-key
recovery, empty selection, save failure/retry, reader outage/reload, duplicate
run, stop/disable, navigation with unsaved choices, partial coverage and escaped
evidence. No browser page errors occurred. The operator key was absent from
local/session storage. The 390px layout had no horizontal overflow.

| Evidence | Revision / source fingerprint |
| --- | --- |
| [Save failure/recovery](evidence/save-recovery.png), [390px controls](evidence/narrow-controls.png) | Base `b39c8193…` plus uncommitted UI; [original browser metadata](evidence/browser-controls.json), JS SHA256 `55100b028ac6f5997c092a626735ca2011523797486c343abcfef742ad4d740c`. |
| [Finding evidence](evidence/finding-evidence.png) | Same base plus corrected UI; [final browser metadata](evidence/browser-evidence.json), JS SHA256 `a72f95738521b3395c152ac068ee91694057a27b57b9b72bfb7fd3bb3aa0e274`. |

Screenshots contain only fictional fixture values. Script-like text in the
evidence screenshot is an intentional escaping test and rendered as plain text.

## Corrections and limits

- Windows fixture directories now use ordinary canonical temporary paths. The production root/handle/link checks remain strict; they were not relaxed to pass a fixture.
- Reader dependencies load separately from the host, so importing the feature does not require a working PostgreSQL native driver when SQL is not used.
- UI refresh now reflects externally saved enable/selection state when there are no unsaved edits. The evidence-state screenshot was rechecked after that correction.
- Operator origin checks compare scheme/authority, supporting a reverse-proxy path prefix while still rejecting cross-origin requests. The audit scheduler is registered independently of the overall-refresh setting.
- Four comparable same-weekday observations are required; missing retained files and legacy transformation provenance may delay baseline coverage. Unsupported formats, schema drift, append boundaries and replica lag remain unverified.
- The model has no write tool or arbitrary SQL interface. Strong filesystem/network containment still requires administrator deployment under the documented separate identities. This application cannot impose isolation on an externally managed inference process.
- Synthetic model responses prove tool validation and evidence flow, not real Qwen detection accuracy. Aggregate checks can miss business-rule errors even with correct counts and totals.

## Merge evidence

Pending at this committed cutoff. The PR for branch
`codex/read-only-data-auditor` carries this package and records the final CI run,
tested head and merge result. No deployed-app claim is made by this report.

## 2026-09-15 08:16 UTC: CI regression correction

The [first CI run](https://github.com/datap0nd/data_governance/actions/runs/34944790601)
tested head `2beb06c4b238453bb5e9c537c36312acafa33d85`. PostgreSQL 14/18,
frontend contracts/syntax and Windows verifier contracts passed. All 14 new
auditor PostgreSQL cases executed on both versions. The combined PG14 suite
reported 26 passed and one existing PostgreSQL-16-only membership case skipped;
PG18 reported 27 passed. The existing Gemini reader case passed on each.

Python shard 0 failed an existing synthetic virtual-calendar case:
`test_range_reacquires_virtualized_cells_after_each_scroll`. It reported
**1 failed, 1,105 passed, 4 skipped, 1,110 deselected** in 519.75 seconds, with
the two existing deprecation warnings. A queued scroll repaint could run after
the next identity snapshot/click and discard the selection. The auditor browser
tests passed in that same shard. At this appendix cutoff, shard 1 was still
running; no result is presumed for it.

The range runtime now waits for scroll handlers and the next rendered frame
before taking another snapshot, with the existing bounded repaint timeout.
Selection assertions were retained, and the existing virtual-list test gained
a deferred-repaint variant. This corrects a race in unchanged controls; it does
not change the demonstrated auditor journey.

Local diagnostic attempt `20260915T081332704Z-2480-a264958c` failed because this
checkout did not yet have Playwright's bundled Chromium; it did not reproduce
the application assertion locally. After installing Chromium/headless shell
151.0.7922.34 with `PLAYWRIGHT_BROWSERS_PATH` set to this checkout's ignored
`.playwright-browsers`, R02 passed: **6 passed, zero skips/warnings, 16.85 seconds**
on `2beb06c4…` plus the uncommitted correction. Evidence:
`20260915T081524295Z-27272-5a006df7`, included in
[local-verification.json](evidence/local-verification.json).

```text
python tools/check.py verify --test tests/test_recording_ranges.py::test_range_reacquires_virtualized_cells_after_each_scroll --test tests/test_recording_ranges.py::test_range_selects_new_week_and_skips_disabled_future_week --test tests/test_recording_ranges.py::test_range_fails_closed_for_duplicate_or_missing_weeks --test tests/test_recording_ranges.py::test_version_three_range_contract_and_legacy_readers --syntax app/flow_recording_runtime.py --syntax tests/test_recording_ranges.py
```

Fresh final-head CI is required for this application/test correction. Its
results and the later first-run shard result belong in the PR testing section;
this appendix preserves what was known at its stated cutoff.

## 2026-09-15 08:34 UTC: scanner fixture isolation correction

The [second CI run](https://github.com/datap0nd/data_governance/actions/runs/34946257591)
tested head `62266df0e123245805885dda6dcb83e6ea523ffd` after integration with
main `1cf0f05d…`. The calendar correction and Python shard 0 passed. Shard 1
reported **1 failed, 1,102 passed, 7 skipped, 1,111 deselected**, 12 existing
deprecation warnings and 610.54 seconds: the existing scanner redaction test
opened notification settings outside its temporary database. All non-Python
gates passed again. The overall run correctly failed its merge gate.

`tests/test_scan_status_consumers.py` now isolates both database consumers in
its helper. Its redaction case deliberately supplies an unavailable default
settings path before creating the fixture, proving it does not depend on a
previous test creating that directory. No application behavior or assertion was
weakened. R03 passed locally: **7 passed, zero skips/warnings, 3.60 seconds**,
head `62266df0…` plus this uncommitted test-only correction. The command is in
the plan and [local-verification.json](evidence/local-verification.json); run
`20260915T083320996Z-21012-9967f376` records its source fingerprint. Final-head
CI remains required and is recorded in the PR after this appendix cutoff.
