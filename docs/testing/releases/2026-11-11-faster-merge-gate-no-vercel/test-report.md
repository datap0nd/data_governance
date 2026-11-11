# 2026-11-11: Faster merge gate and no Vercel deployment: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 12:15
- Tested code revision: `64f281e` on `main` at `058f0fa`; the commit that
  adds this package changes documentation only
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`
- Overall finding: the new split behaves as designed on synthetic lists and
  on a real small pytest run, and divides the recorded run into six equal
  shares. How long a pull request now waits is not known until this pull
  request's own CI run finishes; that number goes in the PR testing section.

## Why the gate was slow

Measured from the JUnit results of pull request run 43010900841:

- 3,073 tests in 151 files took 1,488 seconds of test time.
- They ran on two runners that were balanced by number of tests, 1,537 and
  1,536. One runner got 500 seconds of tests and the other 1,004 seconds, so
  every pull request waited for the slow one.
- A runner spends about one minute on set-up before its tests start.

Time from the workflow starting to `Merge ready` finishing, on today's four
passing pull request runs:

| Run | Start to `Merge ready` | Python jobs |
| --- | --- | --- |
| [42989865519](https://github.com/datap0nd/data_governance/actions/runs/42989865519) | 14 min 38 s | 12 min 48 s, 14 min 06 s |
| [42996582192](https://github.com/datap0nd/data_governance/actions/runs/42996582192) | 15 min 57 s | 15 min 24 s, 11 min 39 s |
| [43010900841](https://github.com/datap0nd/data_governance/actions/runs/43010900841) | 18 min 13 s | 9 min 04 s, 17 min 39 s |
| [43015927738](https://github.com/datap0nd/data_governance/actions/runs/43015927738) | 15 min 33 s | 13 min 47 s, 15 min 04 s |

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| G-01 to G-08 | The `python tools/check.py verify` command in the plan | `64f281e`, Linux | PASS: 41 passed, 0 failed (27 in `tests/test_ci_sharding.py`, of which 7 are new), three syntax checks passed | `.test-runs/20261111T121142668Z-2533-93fdcead/result.json` |
| Split of the recorded run | `partition` over the recorded files at 2, 4, 6 and 8 runners | Recorded times of run 43010900841 | Seconds of tests on the slowest runner: 744 at two runners, 372 at four, 248 at six, 186 at eight. Every runner got the same share in each case | Console output |
| V-01 | `python -c "import json; ..."` on `vercel.json`; `python -c "import app.main"`; `git grep api/index -- app tools tests setup.ps1 update_app.ps1` | `64f281e` | PASS: the file holds only `git.deploymentEnabled: false`; the app imports; no reference remains | Console output |

The `vercel.json` setting is the one Vercel's documentation gives under
"Turning off all automatic deployments"
(vercel.com/docs/project-configuration/git-configuration, read 2026-11-11).

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| G-09, the measured time | NOT RUN | It is this pull request's CI run | Record the start-to-`Merge ready` time and the six job durations in the PR testing section |
| V-02 | NOT RUN | Needs the push | Record on the pull request whether Vercel deployed the head |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

- With six equal shares of about 250 seconds and about a minute of set-up, a
  run should take roughly six minutes instead of 15 to 18. That is an
  estimate from one recorded run; runner availability and set-up time vary.
- The recorded times are from Ubuntu. The same record is used for the Windows
  runs after a merge and at night, where the balance will be less exact.
- A file never timed is placed by its number of tests at the average speed.
  When one runner has drifted well above the others, refresh the record with
  `tools/ci/record_durations.py`; the testing workflow document says how.
- More runners do not reduce the total work: six jobs each pay the one-minute
  set-up, so a run uses slightly more runner minutes than before.
- No test was removed, skipped or changed in what it checks. Which tests are
  worth keeping is a separate analysis.
- Vercel: this change stops new deployments from the repository side.
  Deployments Vercel made earlier stay online, and the Vercel project stays
  connected to the repository, until the owner removes it in Vercel's
  dashboard. That was not done or checked here.

## Final assessment

Ready for final-head CI, which is also the measurement.
