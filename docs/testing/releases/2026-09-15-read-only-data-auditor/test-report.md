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
