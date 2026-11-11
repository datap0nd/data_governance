# 2026-11-11: Installer no longer needs pandas before it installs pandas: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 11:58
- Tested code revision: the commit that adds this package, on `main` at
  `c52d565`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`, pip 26.2. No PowerShell is available here, so `setup.ps1` was not
  executed
- Overall finding: the reported failure is reproduced on a new Python and no
  longer happens with the changed arguments (automated, synthetic). The
  Windows run of the same case, with the xpress9 wheel, is left to the pull
  request's Windows job.

## What was reported

On the work PC, a manual run of `setup.ps1` in an administrator PowerShell
stopped at step 5 with "Could not find a version that satisfies the
requirement pandas (from pbixray)", "No matching distribution found for
pandas" and "SETUP FAILED: Bundled Python dependency installation failed with
exit code 1".

## Cause

The first pip step installs pbixray from `vendor/` with `--no-index`. The
bundled pbixray 0.5.0 declares that it needs xpress9, kaitaistruct, pandas and
apsw. Only the first two are in `vendor/`, so pip had to find pandas and apsw
already installed. That holds on a Python an earlier install has filled, and
fails on a new or rebuilt one. The line has been this way since at least
September; the changes merged earlier today did not touch it.

Why the Python on the work PC had no pandas at that moment is not known from
here. The installer puts its Python beside the folder `setup.ps1` is run from,
so running a copy of the script from a folder other than the installed one
would produce exactly this.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| B-01, B-02, B-04 | The `python tools/check.py verify` command in the plan | Working tree, Linux | PASS: 22 passed, 0 failed, 26 skipped (Windows PowerShell only), 3.5 s | `.test-runs/20261111T115345548Z-436-0bc5fb02/result.json` |
| B-03 | `python -m pytest tests/test_setup_fast_path.py -k bundled` with `setup.ps1` restored from `c52d565`, then put back | New tests, previous script | 2 failed, as intended | Console output |
| Reproduction by hand | New virtual environment; `pip install --no-index --find-links vendor pbixray kaitaistruct`, then the same with `--no-deps`, then `pip install pbixray` with the index | Linux, pip 26.2 | First: "No matching distribution found" (for xpress9 here, since no Linux wheel of it is bundled; on Windows the first missing one is pandas). Second: pbixray and kaitaistruct installed. Third: pandas, apsw and xpress9 installed from the index | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| B-02 on Windows with xpress9 | NOT RUN | Needs Windows | The pull request's `Windows verifier contracts` job runs it |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

- With `--no-deps` the offline step installs only the three bundled wheels.
  The requirements step that follows names pbixray again; pip then installs
  what an already installed package still needs, which the reproduction
  above shows.
- On a Python that already has everything, both forms do nothing, so an
  ordinary update is unchanged.
- Not established here: that the requirements step can reach the package
  index from the work PC. If it cannot, that step fails with its own message,
  "Python dependency installation failed".

## Final assessment

Ready for final-head CI.
