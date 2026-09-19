# Managed auditor ACL recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff (UTC): **2026-09-19 12:52:05 UTC**. Final-head CI follows this committed report and is recorded in the PR before merge.
- Tested code revision: baseline `95cbe04722c0a19a8d1cf554f3b78fab6ce19461` plus the uncommitted implementation with verifier source fingerprint `1929e33c5e2e9fa2059300cadbc93761a51a63072270eb0bb08bd9f528f84c24`.
- Environment: Microsoft Windows `10.0.26200`, PowerShell `7.6.5`, checkout-owned Python `3.13.15`, exact `requirements-ci.lock`.
- Overall finding: the final focused managed-auditor selection and Python/PowerShell syntax checks passed. A real Windows deny ACL was repaired and repeat provisioning preserved the existing secrets. Final-head CI remains pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup prerequisite | `.\tools\check.ps1 -Mode Setup` | Baseline plus implementation; Windows/Python 3.13.15 | PASS; checkout-owned locked environment created. | Run `20260919T124719818Z-44756-941eba9b`. |
| A-01–A-03 | Exact focused verifier command from the plan. | Baseline plus implementation; source fingerprint `1929e33c…`; Windows | **PASS: 7 passed, zero failures/errors/skips, two existing framework deprecation warnings; 0.90s pytest / 2.924s verifier.** | [Local verification record](evidence/local-verification.json); run `20260919T125202466Z-13664-28518bb6`. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| R-01 | NOT RUN | Final-head CI starts after the PR is opened. | Record the exact head SHA and `Merge ready` run in the PR before merging. |

## Findings, limitations and retests

The supplied failure shows `PermissionError` while the provisioner reads the
managed `host.json`, before setup reapplies its intended ACLs. The first real
Windows ACL regression reproduced a deeper cause: combining inheritance and
grant changes in one `icacls` invocation returned success while leaving the
file without effective access. Run `20260919T124839649Z-37084-4ffc78e9` retained
that failure (1 failed, 6 passed). Three failed selector diagnostics then
localized the ordering problem (`20260919T124919113Z-48140-022eb78c`,
`20260919T124948996Z-38212-f890ba79`, and
`20260919T125046517Z-39204-5f387fb5`).

The final sequence resets each managed directory, removes inherited access,
then grants explicit stable-SID access in separately checked commands. The
failed selector passed on run `20260919T125152313Z-14272-5bc02f9e`; the complete
affected file then passed canonically. The reader stays stopped during the
repair, temporary installer access is removed from its directory afterward,
and the provisioner can recover the shared token from the peer config if one
old file still raises `PermissionError`. The two warnings are the repository's
existing Starlette/httpx TestClient and AnyIO BlockingPortal deprecations.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR testing section must record the final head SHA, run URL, required result, and
merge outcome. No deployment is claimed.
