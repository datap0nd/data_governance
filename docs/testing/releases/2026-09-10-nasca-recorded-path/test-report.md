# 2026-09-10 NASCA recorded-path preservation: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10T19:48:00Z
- Tested revision: baseline `c3fadd2744f4e3db69cb53a633b0517ee601401a` plus the uncommitted recorded-path implementation and test
- Environment: Windows 10; checkout-owned Python 3.13.15; synthetic Playwright browser and NASCA/Excel fixture; explicitly requested BI desktop
- Overall finding: recorded ASAP playback copied the stable browser download to a UUID path before Excel recovery. The fix preserves the exact staging path through validation.

## Executed checks

| Case | Procedure | Revision/environment | Result | Evidence |
| --- | --- | --- | --- | --- |
| T-LIVE-BASELINE | On installed build `c3fadd274`, replayed unchanged MTracker_subs recording revision 46 with SQL disabled. | BI desktop; recording test 160 | FAIL; download completed and reached Excel recovery, but Excel rejected the UUID copy. No SQL ran. | `LIVE-MTRACKER-20260910-H` |
| DB-BASELINE | SELECT-only pgAdmin metadata and aggregate checks before any production retry. | BI desktop database | PASS; 145,990 rows, 14 expected columns, years 2024–2026, 15 countries, and zero nulls in country/model/qty. | `LIVE-MTRACKER-DB-20260910-A` |
| T-01 initial | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_recording_optional_checks.py::test_recording_validation_normalizes_nasca_input_for_configured_sql -SyntaxPath app/flow_recording_runtime.py,tests/test_recording_optional_checks.py` | Uncommitted implementation; Windows; Python 3.13.15 | FAIL; the ASCII-only protected fixture classified as text after the source copy was correctly removed, so the Excel spy was not called. | `.test-runs/20260910T192306916Z-14920-28bc758b/result.json` |
| T-01 failed-case rerun | Same command after representing the real OLE-wrapped NASCA signature. | Final source/test fingerprint before commit; Windows; Python 3.13.15 | PASS; 1 passed in 8.21s; syntax passed; no skips or warnings. The Excel recovery input exactly matched the staging-completion path. | `.test-runs/20260910T192401482Z-24916-b6fa7ce7/result.json` |
| Initial final-head CI | GitHub Actions full Python suite on `300ade35f4274edafbaada272e70cb8948bf7882`. | Ubuntu; Python 3.13.15 | FAIL; 1,945 passed, 23 skipped, 2 failed, 12 warnings in 906.10s. A binary-mislabeled unchecked download exposed the broader preservation condition; the separate Edge portable case exceeded its 90-second subprocess limit. | `https://github.com/datap0nd/data_governance/actions/runs/34520433575` |
| Failed cases plus T-01, combined attempt | Focused verifier with both CI failures and T-01. | Revised uncommitted source; Windows; Python 3.13.15 | FAIL; T-01 passed, while the two portable cases collided on a shared generated Flow folder in the combined process. | `.test-runs/20260910T194532353Z-24204-91546853/result.json` |
| Binary-mislabeled failed-case rerun | Exact CI failure in an isolated verifier process, with runtime and test syntax checks. | Revised uncommitted source; Windows; Python 3.13.15 | PASS; 1 passed in 12.12s; syntax passed. Preservation is now limited to Excel downloads requiring tabular processing. | `.test-runs/20260910T194606988Z-23588-dbd9508a/result.json` |
| Edge timeout failed-case rerun | Exact `msedge` portable selector in an isolated verifier process. | Revised uncommitted source; Windows; Python 3.13.15 | BLOCKED; the checkout host has no Playwright `msedge` distribution. Final-head CI provides the browser-enabled environment. | `.test-runs/20260910T194637558Z-38932-990f3234/result.json` |
| T-01 revised-source rerun | Exact NASCA staging-path regression with runtime/test syntax checks. | Revised final source fingerprint before commit; Windows; Python 3.13.15 | PASS; 1 passed in 7.85s; syntax passed; no skips or warnings. | `.test-runs/20260910T194706186Z-36392-1d782a95/result.json` |

## Pending in-scope checks

| Cases | Status | Next action |
| --- | --- | --- |
| Final-head CI | NOT RUN | Open the PR, wait for `Merge ready`, and record its run URL and exact head SHA before merge. |
| T-02 | NOT RUN | Merge and install the final head, then replay the unchanged recording once. |
| T-03/T-04 | BLOCKED | Run production exactly once and perform SELECT-only checks only after T-02 passes. |

## Review and limitations

A bounded diff review confirmed only the ASAP/staging-completion branch skips
the UUID copy; native browser-download handling retains its existing isolated
copy. The authoritative staged file is still normalized and published through
the existing validation, checksum and retention paths. Synthetic fixtures do
not emulate the corporate NASCA provider, so the requested live retest remains
decisive.

## Merge evidence

Pending. Record the final `Merge ready` URL and exact PR head SHA in the PR
testing section before a head-pinned merge.
