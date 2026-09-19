# Setup worker timestamp recovery: test plan

- Change/PR: prevent Windows setup from failing after successful service startup when the worker API response is delivered as a nested `System.Object[]` or contains an invalid timestamp.
- Code baseline: `6dcebc5dafcd0f70899403ab2de71baf7699fc89`; the PR records its final tested head.
- Related report: [test-report.md](test-report.md)
- Intended environments: isolated Windows verification with Windows PowerShell 5.1, Python 3.13 and final-head CI.

## Prerequisites and test data

Use the checkout-owned Python 3.13 environment created by `tools/check.ps1`.
The fixture defines a fictional two-worker REST response entirely in memory and
does not start services, use browser profiles, access credentials, or call an
external endpoint.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| W-01 | In Windows PowerShell 5.1, mock `Invoke-RestMethod` so it emits a two-item worker array with `Write-Output -NoEnumerate`, matching the nested response semantics from the failure. Call `Get-MetronomeSetupFlowWorkers`. | The helper returns two individual worker objects rather than one nested `System.Object[]`. | Focused verifier result. |
| W-02 | Check a recent scalar ISO timestamp through `Test-MetronomeSetupWorkerFresh`. | The worker is considered fresh without a conversion error. | Focused verifier result. |
| W-03 | Pass an array-valued malformed timestamp to the same helper. | The helper returns false; setup can report the worker as stale instead of terminating. | Focused verifier result. |
| W-04 | Review every worker-registration poll and diagnostic path in `setup.ps1`. | All paths use the common flattening and safe timestamp helpers; no direct `[datetime]` cast remains. | Diff review. |
| C-01 | Exercise the synthetic Flow topic-group activity poll after committing completed run state, allowing for one stale in-flight response and the following five-second poll cycle. | The execution pane closes, editor work is preserved, and the check has sufficient loaded-runner scheduling margin. | Focused CI-recovery verifier result. |
| R-01 | Run required final-head CI on the PR. | `Merge ready` passes for the exact recorded head SHA. | PR testing section and CI run. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_setup_worker_health.py::test_worker_health_flattens_rest_array_and_rejects_bad_timestamps,tests/test_flows.py::test_setup_installs_headless_flow_worker_service `
  -SyntaxPath setup.ps1,tools/setup_flow_worker_health.ps1

# Only if the synthetic activity-poll case fails in final-head CI:
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flow_topic_groups_browser.py::test_actual_group_queue_polling_completion_failure_and_individual_recovery
```

Run one bounded diff review covering response flattening, valid and malformed
timestamps, every registration call site, setup self-update availability, and
the missing-helper failure message. Final-head CI owns the full regression.

## Acceptance and cleanup

Accept when the Windows PowerShell 5.1 behavior test and setup source contract
pass, both PowerShell files are syntax-clean, the review finds no unguarded
registration timestamp conversion, and required final-head CI passes. Verifier
scratch data is removed according to `tools/check.ps1`; rollback is a code-only
revert of the setup helper integration.
