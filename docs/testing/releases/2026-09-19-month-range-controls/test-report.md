# Monthly range controls in recorded Flows: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: pending.
- Evidence cutoff (UTC): 2026-09-19 10:31; final-head CI is pending.
- Tested code revision: working tree based on `7e2d85e93af62219649212d8adecf2a830b246eb`; the implementation will be committed unchanged after this report.
- Environment: Windows, PowerShell, checkout-owned Python 3.13.15 `.venv`, Playwright 1.62.0 using the repository's Chrome-first launcher.
- Overall finding: focused local verification PASS after correcting test-only isolation/visibility fixtures. The live pre-fix attempt confirmed both the portal path and the recorder limitation. No post-fix live portal run has occurred because the change is not yet on `main`.

## Executed checks

| Check/case IDs | Command or procedure | Result | Evidence |
| --- | --- | --- | --- |
| Setup | `tools/check.ps1 -Mode Setup` | PASS; checkout-owned Python 3.13 environment created from the lock file. | `.test-runs/20260919T102048831Z-40576-2550a54e/result.json` |
| C-01 to C-03, P-01 to P-03, E-01 to E-03; affected syntax | Plan's focused `tools/check.ps1 -Mode Verify` command. | PASS: 44 passed, 0 failed in 27.34 s. Automated Chrome verified the month authoring journey and a nested noUi-style handle; playback expanded `202608`/`202609` to `202601`/`202609` and proved exact read-back. | `.test-runs/20260919T103020419Z-8044-30a57b1a/result.json` |
| Failed-case retests during implementation | Exact failing selectors were rerun after fixture corrections. | PASS: month target visibility and both v4/v5 worker gates passed. The final 44-case run supersedes these diagnostic runs. | `.test-runs/20260919T102901215Z-35364-db135d32/result.json` and earlier diagnostic result files |
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
