# Setup worker timestamp recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff (UTC): **2026-09-19 16:44:43 UTC**. Fresh final-head CI follows this committed report and is recorded in the PR before merge.
- Tested code revision: setup correction `92ed759692e3be1822a40e27790018a8f0ec3f85` plus the uncommitted synthetic timing correction with recovery verifier source fingerprint `081bd4c4852a47a18e7c5142181d0206269bb2de571cb9693fe40c92eb4402ff`.
- Environment: Microsoft Windows `10.0.26200`, PowerShell `7.6.5` verifier, Windows PowerShell `5.1.26100.9444` behavioral subprocess, checkout-owned Python `3.13.15`, exact `requirements-ci.lock`.
- Overall finding: the isolated Windows PowerShell reproduction, setup source contract, and failed synthetic CI companion retest passed. Fresh final-head CI remains pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup prerequisite | `.\tools\check.ps1 -Mode Setup` | Baseline plus correction; Windows/Python 3.13.15 | PASS; checkout-owned locked environment created. | Run `20260919T161231699Z-36572-69f57d9c`. |
| W-01–W-04 | Exact focused verifier command from the plan. | Baseline plus correction; source fingerprint `3cb57d5f…`; Windows PowerShell 5.1 subprocess | **PASS: 2 passed, zero failures/errors/skips; both PowerShell syntax checks passed; 1.58s pytest / 5.264s verifier.** | [Local verification record](evidence/local-verification.json); run `20260919T161439704Z-39580-ed440e31`. |
| Initial final-head CI | Full selected PR suite, attempts 1 and 2. | `92ed759692e3be1822a40e27790018a8f0ec3f85`; Ubuntu/Python 3.13.15 | **FAIL:** the same synthetic topic-group browser check exceeded its 10-second assertion in both attempts; setup-related Windows verifier, frontend, and the other Python shard passed. | [Attempt 1 job](https://github.com/datap0nd/data_governance/actions/runs/35454450886/job/105927174323); [attempt 2 job](https://github.com/datap0nd/data_governance/actions/runs/35454450886/job/105929598135). |
| C-01 | Focused recovery command from the plan after allowing one stale poll plus the following cycle. | `92ed759…` plus timing correction; fingerprint `081bd4c4…`; synthetic Chrome on Windows | **PASS: 1 passed, zero failures/errors/skips; 2 existing dependency warnings; 6.99s test / 23.076s verifier.** | [CI-recovery verification](evidence/ci-recovery-local.json); run `20260919T164419470Z-34300-fe5d5611`. |

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

Both initial hosted attempts exposed a separate deterministic CI timing issue:
the synthetic activity assertion allowed exactly two five-second intervals. An
already in-flight response may legitimately return the pre-commit snapshot,
leaving no margin for the following scheduled poll under runner load. The test
now allows 20 seconds—one stale response, the next full cycle, and scheduling
margin—without changing the application's five-second polling behavior. The
focused recovery passed; both hosted failures remain recorded above.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR testing section must record the final head SHA, run URL, required result, and
merge outcome. No deployment is claimed.
