# 2026-11-17: A slow worker start no longer stops setup: test plan

- Change/PR: link in the PR's testing section. `setup.ps1` starts the Flows
  workers, the auditor reader and Metronome through
  `Start-MetronomeSetupService` (in `tools/setup_flow_worker_health.ps1`). The
  helper reads NSSM's message instead of letting Windows PowerShell stop setup
  on it.
- Code baseline: `main` at `2283eb4`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container for the static check; the
  pull-request "Windows verifier contracts" job (Windows PowerShell 5.1) for
  the PowerShell case.

## The defect

Reported from the work PC: setup ended with `SETUP FAILED: MXFlowsWorker3:
Unexpected status SERVICE_START_PENDING in response to START control.` at
`setup.ps1` line 1302, before Metronome itself was started.

The start line was `& $NssmExe start <slot> 2>&1 | Out-Null` under
`$ErrorActionPreference = "Stop"`. With stderr redirected, Windows PowerShell
5.1 turns NSSM's stderr line into an error, which stops the script. NSSM writes
that line when Windows still reports the service as starting after NSSM's own
wait. That happens on a busy PC, right after setup has signed in several
browsers. The two other redirected starts (the main worker and the auditor
reader) had the same exposure.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| S-01 | Windows PowerShell, `$ErrorActionPreference = 'Stop'`, a fake `nssm` that prints the reported START_PENDING line to stderr and exits 1; run the old start line | The old line stops with that message (the defect, reproduced) | `tests/test_setup_worker_health.py::test_a_slow_service_start_no_longer_stops_setup` |
| S-02 | Same session: `Start-MetronomeSetupService` with fakes for START_PENDING, "already running", a clean start and SERVICE_PAUSED | States Starting, Running, Started and Failed. Nothing throws, the caller's error preference is unchanged, and each line printed matches its state; a clean start prints nothing | Same test |
| S-03 | Static check of `setup.ps1` | No service is started with `& $NssmExe start`. The worker, every slot, the auditor reader and Metronome start through the helper, which is loaded before the first start | `tests/test_setup_worker_health.py::test_setup_starts_every_service_through_the_tolerant_helper` |
| R-01 | The other setup contract tests | Pass on Linux where they can run; the Windows job runs the rest | The `verify` command below |

## Automated checks

```bash
python tools/check.py verify --test tests/test_setup_worker_health.py --test tests/test_setup_install_guard.py --test tests/test_setup_fast_path.py --test tests/test_unattended_update_scripts.py --test tests/test_setup_sign_in_browsers.py
```

S-01 and S-02 need Windows PowerShell and run in the pull-request
"Windows verifier contracts" job. Final-head CI is the full regression.

## Acceptance and cleanup

Accepted when S-01 to S-03 pass on the final head and `Merge ready` is green.
Nothing to clean up. Rollback: revert the PR; setup then stops again on a slow
service start, and starting the services by hand still works.
