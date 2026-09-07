# Setup shared SSO password: test plan

- Scope: setup refreshes the enrolled ASAP/GSCM password from `DG_SVC_PASSWORD`
  before browser authentication, retaining the existing portal user ID.
- Baseline: `e0353e0d`; implementation: `87bf76d902f5ea334bcd25ced7c850f20774b2b7`.
- Related report: [test-report.md](test-report.md).
- Environments: Windows PowerShell 5.1 / Python 3.13; Linux Python 3.13 for
  portable regression tests; work-PC ASAP and GSCM for live confirmation.

## Prerequisites and test data

Use isolated temporary profiles and fictional credentials for automated tests.
The Windows integration tests execute only the actual synchronization block
from setup with a real DPAPI file; they never install services or contact a portal.

For live checks, wait until no Flow is active, obtain the merged setup version,
and use the Windows account that owns the existing encrypted portal credential.
ASAP must already have a user ID enrolled through Flows > Catalog > ASAP.
Open elevated Windows PowerShell in the installed code folder. Supply the current
shared password to that process's `DG_SVC_PASSWORD` through a local masked prompt
or the existing environment-variable workflow. Do not publish its value.
Changing a saved User/Machine variable does not refresh an already-running
PowerShell process's copy. Keep any real evidence in protected local storage.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| SSO-01 | Run `tests/test_setup_sso_password.py`; rotate a fictional enrolled password containing quotes, spaces, shell punctuation and Unicode. | Exact new password is recovered from the encrypted record; portal user ID stays unchanged even when the Windows username differs; no secret in console output. | Pytest/JUnit result. |
| SSO-02 | Run cases with the variable missing and empty. | Existing file remains byte-for-byte unchanged. | Pytest result. |
| SSO-03 | Supply the variable to an empty temporary profile. | No account is guessed or file created; output directs enrollment through Flows > Catalog > ASAP. | Pytest result. |
| SSO-04 | Simulate decryption/encryption errors; execute the real PowerShell block with a corrupt temporary record. | Sanitized error and nonzero exit; old file preserved; authentication sentinel never reached. | Pytest result; no raw secret-bearing exceptions. |
| SSO-05 | Run the real PowerShell block with a valid isolated DPAPI record. | Child helper inherits the exact password; updated record decrypts correctly and retains user ID; authentication sentinel reached. | Windows pytest result. |
| SSO-06 | Review setup order and run the existing credential/updater tests. | Sync occurs before portal authentication and outside the interactive-only guard; existing service/updater contracts continue to pass. | Tests and PowerShell parse. |
| LIVE-01 | On the work PC with a previously enrolled older portal password, run `powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -File .\setup.ps1` with the current variable supplied. | Setup prints `ASAP/GSCM password refreshed from DG_SVC_PASSWORD; saved user ID preserved.` before authentication; ASAP and GSCM authenticate with the current password in configured browser profiles. Services/updater register successfully. | UTC, installed revision, browser versions, sanitized outcome for each portal/profile, opaque evidence ID. |
| LIVE-02 | On a test installation, run setup without the variable and with a valid saved credential. | Portal credential is preserved; existing authentication behavior remains. | Revision and sanitized result. |
| LIVE-03 | On a test installation, run `powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -File .\setup.ps1 -Unattended` with the variable and enrolled ID. | Password refresh occurs; no browser opens; services resume normally. | Revision and sanitized result. |
| LIVE-04 | On a disposable test installation, use an invalid encrypted record, run setup with the variable, then repair it using Flows > Catalog > ASAP and rerun. | First run stops before portal authentication with clear recovery guidance; repaired run authenticates. Use local helper tests if stopping the test service makes the UI unavailable; do not corrupt production credentials. | Sanitized failure/recovery result and revision. |

## Automated checks

```powershell
python -m pytest tests/test_setup_sso_password.py tests/test_flow_credentials.py tests/test_unattended_update_scripts.py -q
python -m pytest tests -q
```

On the local Windows ARM host, use the documented pytest convenience-symlink
workaround and an external temporary directory:

```powershell
python -u -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests','-q','--tb=short','--basetemp='+tempfile.gettempdir()+'/metronome-sso-full-'+uuid.uuid4().hex,'--junitxml=.test-setup-sso-full.xml']))"
```

Parse setup without executing it:

```powershell
$parseTokens=$null
$parseErrors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) 'setup.ps1'), [ref]$parseTokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { $parseErrors; throw 'PowerShell parse failed' }
git diff --check
```

## Acceptance and cleanup

Automated acceptance requires focused regressions, Windows DPAPI/PowerShell
integration, full Python suite and final PR CI. Live portal acceptance is recorded
separately; CI cannot prove corporate SSO success.

This change adds no UI controls, configuration form, or new interactive journey;
the existing setup invocation consumes an already supplied variable. A clickable
browser preview is not applicable. Console success/failure and the preservation
of the enrolled user ID are covered by the isolated executable tests.

Keep the current password in the real encrypted credential. Remove only task-owned
fictional profiles and temporary process variables when no longer needed. Never
restore an expired password or publish credential files. To opt out of future
setup synchronization, omit `DG_SVC_PASSWORD`; existing portal credentials remain.
A code rollback does not restore the previous credential value. Repair credentials
through the existing ASAP dialog if necessary; avoid repeated real failed logins.
