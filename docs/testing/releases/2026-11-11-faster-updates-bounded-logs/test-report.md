# 2026-11-11: Faster updates and bounded service logs: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 08:30
- Tested code revision: the working tree this report is committed with, on
  `main` at `bd09c94`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv` with the locked CI dependencies, git 2.43, Node 22.23. No PowerShell
  is available in this environment
- Overall finding: every case that can run on Linux passes (automated,
  synthetic fixtures). The Windows PowerShell cases W-01 to W-06 have not run
  yet; they run in the pull request's Windows job. `setup.ps1` was not
  executed anywhere.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| L-01 to L-09, S-01 to S-06, R-01 | The `python tools/check.py verify` command in the plan | Working tree above, Linux | PASS: 180 passed, 0 failed, 30 skipped, 2 third-party deprecation warnings, 93.6 s; four syntax checks passed | `.test-runs/20261111T082719418Z-8472-2ac00a2a/result.json` |
| N-01 | `python -m pytest tests/test_setup_fast_path.py` with `setup.ps1`, `.gitattributes` and `tools/apply_update.ps1` restored from `bd09c94`, then put back | New tests, previous scripts | 7 failed, 4 passed, 23 skipped: S-01 to S-06 and the worker-poll contract fail against the previous files, as intended | Console output |
| Brace balance | `test_every_block_setup_opens_is_closed` | Working tree | PASS for all 17 scripts. `setup.ps1` holds 347 opening and 347 closing braces (274 each at `bd09c94`) | Same result file |
| Frontend contracts | `for f in tests/test_*.mjs; do node "$f"; done` | Working tree | All pass except `tests/test_gemini_extension.mjs`, which stops with `ERR_MODULE_NOT_FOUND` because the extension's npm packages are not installed here. No JavaScript changed in this PR | Console output |

The 20 `setup.ps1` tests in R-01 are the same set the 2026-11-04 package
lists: nine in `tests/test_flows.py`, one in `tests/test_gscm.py`, and the
`setup.ps1` cases in `tests/test_auditor_managed.py`, `tests/test_env_file.py`,
`tests/test_flow_capacity.py`, `tests/test_flow_desktop_session.py`,
`tests/test_flow_worker_startup.py` and `tests/test_setup_sso_password.py`.
This run executed those files whole.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| W-01 to W-06 | NOT RUN | They need Windows PowerShell; this environment has none | The pull request's `Windows verifier contracts` job runs them; record its result in the PR testing section |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

**What caused the log growth.** `setup.ps1` set `AppRotateSeconds 86400` with
`AppRotateBytes 10485760`. nssm reads that as "do not rotate a file written to
in the last 86400 seconds", and only checked it when a service started. A
running service writes to its log all the time, so no log was ever rotated,
and nssm never deletes a rotated file in any case. This is read from the nssm
2.24 documentation and the script, not observed on the work PC.

**What is not proven here.** No test runs `setup.ps1`, a Windows service, a
scheduled task or an ACL change. The skipped-step logic is covered by text
contracts and, on Windows, by the helper functions it calls. The first update
that installs this change does every step in full, because no fingerprint has
been recorded yet; the skips first apply to the update after it.

**How the first update behaves.** The `setup.ps1` already on the PC downloads,
stops and extracts as before, then relaunches the new copy, which resumes
after extraction. That run applies the new rotation settings and starts the
services, and nssm rotates any log over 10 MB as each service starts. Two
minutes after Metronome starts, the clean-up deletes rotated logs older than
14 days and then the oldest until the folder is under 1 GB.

**Deleted data.** That first clean-up deletes most of the existing log
history. Nothing outside `*.log` files directly inside the `logs` folder, and
old transcripts and receipts under `updates`, is touched.

**Skips.** 30 skips, all Windows-only: the 23 PowerShell cases added here
(17 of them one parse case per script), the scheduled updater (1), the worker
health response (1), Windows PowerShell with real DPAPI (2), the Windows ACL
contract (1) and PowerShell service configuration (2).

**Bridge version check.** `tools/apply_update.ps1` compared `VERSION` with
`EndsWith(<40-character commit>)` while `setup.ps1` stamps the first nine
characters, so the bridge reported a failed receipt after a successful
update. S-05 and W-06 cover the corrected rule. Whether the Updates page on
the work PC has been showing those failures was not checked.

## Merge evidence

Pending: final-head CI, including the Windows job for W-01 to W-06. The PR
testing section records the run URL, tested head SHA and result before a
head-pinned merge.
