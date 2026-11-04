# 2026-11-04: Film removed and media/ kept out of updates: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-04 09:15
- Tested code revision: `e4aa4d7` (repository, `.gitattributes` and test
  changes; this report is committed after it)
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv` with the locked CI dependencies, git 2.43.0
- Overall finding: every case in the plan passes locally (automated,
  synthetic fixture). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| M-01, R-01 | `python tools/check.py verify --test tests/test_unattended_update_scripts.py` | `e4aa4d7`, Linux above | PASS: 8 passed, 0 failed, 1 skipped (the Windows scheduled-updater test), 1.4 s | `.test-runs/20261104T091201925Z-638-6bc98df8/result.json` |
| M-02 | The new test run with `.gitattributes` emptied, then restored | Same code, Linux above | 1 failed with the rule removed; 1 passed with it restored | Console output |
| M-03 | `git archive --format=zip HEAD` | `e4aa4d7` | 12.6 MB, 1,055 entries, none under `media/`. The same archive of `main` at `f0defc8` is 70.5 MB | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

Test removed with the content it checked: `tests/test_film_package.py`. It
verified that the film's artifacts matched its manifest and that its HTML
referenced existing files; with the film deleted it has nothing to check.

Historical release packages for the film (2026-09-28 and 2026-09-29) keep
their original text, so their links into `media/` now point at files that
exist only in git history.

M-01 uses local `git archive`. GitHub's download is built the same way, but
this report does not include a download from GitHub itself.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
