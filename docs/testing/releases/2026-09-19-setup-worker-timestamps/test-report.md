# Setup worker timestamp recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff (UTC): **2026-09-19 16:14:45 UTC**. Final-head CI follows this committed report and is recorded in the PR before merge.
- Tested code revision: baseline `6dcebc5dafcd0f70899403ab2de71baf7699fc89` plus the uncommitted correction with verifier source fingerprint `3cb57d5fc93858dbbeca2f140ad3dd0b9bdf64f917bd9abe125a5c96873a87f3`.
- Environment: Microsoft Windows `10.0.26200`, PowerShell `7.6.5` verifier, Windows PowerShell `5.1.26100.9444` behavioral subprocess, checkout-owned Python `3.13.15`, exact `requirements-ci.lock`.
- Overall finding: the isolated Windows PowerShell reproduction and setup source contract passed. Final-head CI remains pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup prerequisite | `.\tools\check.ps1 -Mode Setup` | Baseline plus correction; Windows/Python 3.13.15 | PASS; checkout-owned locked environment created. | Run `20260919T161231699Z-36572-69f57d9c`. |
| W-01–W-04 | Exact focused verifier command from the plan. | Baseline plus correction; source fingerprint `3cb57d5f…`; Windows PowerShell 5.1 subprocess | **PASS: 2 passed, zero failures/errors/skips; both PowerShell syntax checks passed; 1.58s pytest / 5.264s verifier.** | [Local verification record](evidence/local-verification.json); run `20260919T161439704Z-39580-ed440e31`. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| R-01 | NOT RUN | Final-head CI starts after the PR is opened. | Record the exact head SHA and `Merge ready` run in the PR before merging. |

## Findings, limitations and retests

Windows PowerShell 5.1 can preserve a top-level REST JSON array as one pipeline
item. Accessing `last_seen_at` on that nested item performs member enumeration,
producing another `System.Object[]`; the previous direct `[datetime]` cast then
raised the exact conversion error reported after the services had started.

The behavioral fixture reproduced that array shape, then passed after the
response was explicitly re-emitted as individual worker rows. A valid recent
timestamp remained fresh, while an array-valued invalid timestamp returned
false without throwing. A bounded diff review confirmed that all three worker
API reads and every freshness decision use the common helpers. The helper file
is present in the downloaded archive before the freshly downloaded setup script
is relaunched; absence produces a direct missing-helper error. No service,
application, browser, or authentication behavior changed.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR testing section must record the final head SHA, run URL, required result, and
merge outcome. No deployment is claimed.
