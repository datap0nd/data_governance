# Managed auditor ACL recovery: test plan

- Change/PR: make repeated `setup.ps1` runs recover stale managed-auditor ACLs before refreshing configuration, without weakening the final reader boundary.
- Code baseline: `95cbe04722c0a19a8d1cf554f3b78fab6ce19461`; the PR records its final tested head.
- Related report: [test-report.md](test-report.md)
- Intended environments: isolated Windows/Python fixtures and final-head CI.

## Prerequisites and test data

Use the checkout-owned Python 3.13 environment created by `tools/check.ps1`.
Tests use only temporary auditor folders and fictional tokens. They do not read
or change an installed Metronome instance, Windows services, portal sessions,
business exports, or production credentials.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| A-01 | Provision temporary host/reader configs, make the host read raise `PermissionError`, then provision again. | Provisioning completes; the reader copy preserves the shared token and category key; host and reader remain synchronized. | Focused pytest result. |
| A-02 | Inspect the setup contract around the managed auditor directories and provisioner call. | Setup repairs only `host`, `reader`, and `exchange` before reading old files; ACL principals use stable SYSTEM, Administrators, and current-installer SIDs; least-privilege ACLs are reapplied afterward. | Focused pytest and PowerShell syntax result. |
| A-03 | Run the existing managed-auditor tests for repeat provisioning, credential separation, CLI identity selection, exact-path policy, and public readiness. | Existing generated secrets remain stable; uploader credentials remain excluded; the restricted-reader boundary is unchanged. | Focused pytest result. |
| R-01 | Run required final-head CI on the PR. | `Merge ready` passes for the exact recorded head SHA. | PR testing section and CI run. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_auditor_managed.py `
  -SyntaxPath tools/provision_auditor.py,setup.ps1
```

Run one bounded diff review covering the permission failure, recovery window,
secret preservation, and final ACL restoration. Do not duplicate the focused
selection with a broader local suite; final-head CI owns the full regression.

## Acceptance and cleanup

Accept when the focused verifier passes, the diff review finds no widened final
access, and required final-head CI passes. Verifier scratch data is isolated and
removed according to `tools/check.ps1`; no system ACL or service cleanup is
required. Rollback is a code rollback only and must not delete auditor secrets,
history, or Flow outputs.
