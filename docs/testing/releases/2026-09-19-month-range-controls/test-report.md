# Monthly range controls in recorded Flows: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: [#137](https://github.com/datap0nd/data_governance/pull/137).
- Evidence cutoff (UTC): 2026-09-19 11:05; final-head CI is pending.
- Tested code revision: `5ccbe53d045f90b4ab6a4525ed0e5b8c9450ee3f` plus the test-capability expectation corrections described below; the PR records the exact final head.
- Environment: Windows, PowerShell, checkout-owned Python 3.13.15 `.venv`, Playwright 1.62.0 using the repository's Chrome-first launcher.
- Overall finding: focused local verification PASS after correcting test-only isolation/visibility fixtures. The live pre-fix attempt confirmed both the portal path and the recorder limitation. No post-fix live portal run has occurred because the change is not yet on `main`.

## Executed checks

| Check/case IDs | Command or procedure | Result | Evidence |
| --- | --- | --- | --- |
| Setup | `tools/check.ps1 -Mode Setup` | PASS; checkout-owned Python 3.13 environment created from the lock file. | `.test-runs/20260919T102048831Z-40576-2550a54e/result.json` |
| C-01 to C-03, P-01 to P-03, E-01 to E-03; affected syntax | Plan's focused `tools/check.ps1 -Mode Verify` command. | PASS: 44 passed, 0 failed in 27.34 s. Automated Chrome verified the month authoring journey and a nested noUi-style handle; playback expanded `202608`/`202609` to `202601`/`202609` and proved exact read-back. | `.test-runs/20260919T103020419Z-8044-30a57b1a/result.json` |
| Failed-case retests during implementation | Exact failing selectors were rerun after fixture corrections. | PASS: month target visibility and both v4/v5 worker gates passed. The final 44-case run supersedes these diagnostic runs. | `.test-runs/20260919T102901215Z-35364-db135d32/result.json` and earlier diagnostic result files |
| First full CI attempt | Full `Tests` workflow on `4fdec5a`. | FAIL: three new tests assumed the Windows verifier's explicit `DG_FLOWS_ROOT` on Linux. The test helper was made conditional; the three exact selectors then passed locally. | [Run 35437737274](https://github.com/datap0nd/data_governance/actions/runs/35437737274); `.test-runs/20260919T104611220Z-28928-19ccd1c8/result.json` |
| Second full CI attempt | Full `Tests` workflow on `5ccbe53d045f90b4ab6a4525ed0e5b8c9450ee3f`. | FAIL: shard 0 had 2 failed, 1131 passed and 25 skipped because two existing worker-claim tests stopped at v4 while recorder/validation jobs now truthfully advertise v5. Shard 1, frontend and Windows contracts passed. The expectations were updated without changing application behavior. | [Run 35438310785](https://github.com/datap0nd/data_governance/actions/runs/35438310785) |
| Second-CI exact failed-case retests | Separate focused `tools/check.ps1 -Mode Verify` calls for the failed record-claim and validation-claim selectors, with `tests/test_recording_v2_model.py` syntax. | PASS: 1 passed in 2.85 s and 1 passed in 2.58 s. A preceding combined local invocation had 1 pass and 2 fixture-folder collisions because the Windows verifier intentionally shares one filesystem root inside a check run; running each failed selector in its isolated check root removed that test-environment collision. | `.test-runs/20260919T110411276Z-46144-6575cc8e/result.json`; `.test-runs/20260919T110422629Z-42800-3715e770/result.json`; failed diagnostic `.test-runs/20260919T110339132Z-44600-c5de5594/result.json` |
| L-01 live pre-fix baseline | In the owner-requested Retail/SMS Monthly Performance flow, record through the Product export, set raw input choices, expand the month slider to its full visible range, run and download. | PASS for the manual portal export: full range `202601`–`202609`, requested report options, run and raw-data download completed. Recorder imported 46 actions but represented the slider as a generic click on a `.noUi-touch-area`; its existing range editor offered only week/date semantics and a manually chosen parent level. No draft was saved or activated. | Protected evidence `LIVE-SMS-20260919-01`; no private URL, raw report or trace committed. |

## Failure and recovery observations

- Automatic discovery climbs from the exact recorded touch target and accepts the first ancestor containing exactly two visible slider handles. Zero, one or more than two handles through six levels fails closed before download.
- Month extremes use Home/End, wait for a settled value, confirm the extreme with a second key press and compare declared ARIA limits when present. The exact pair is read back again after movement.
- Initial focused verification had four failures: two stale expectations/isolation fixtures, one hidden synthetic touch target and one duplicated isolation setup. No application defect was found in those failures. The failed selectors passed after fixture correction, followed by the clean 44-case run above.
- A bounded review of the actual diff covered month parsing/offsets, extreme selection, automatic-container failure, editor kind transitions, old recording compatibility and v5 worker claim gates. No open finding remains at this cutoff.

## Usability evidence

The owner supplied the exact journey and explicitly requested no visible UI preview. The automated Chrome editor test therefore exercised the existing fictional fixture without opening a preview for the owner: a recorded handle click converts to a range, **Months (YYYYMM)** selects oldest-to-newest defaults, automatic element detection is visible as the default, the saved definition validates, and the narrow viewport does not overflow. This is automated journey evidence, not a user-facing preview approval claim.

## Pending merge evidence

Final-head `Merge ready` is pending. Its run URL, exact tested head SHA and result must be added to the PR testing section before a head-pinned merge. The report intentionally stops at the committed evidence cutoff; later CI evidence belongs in the PR.
