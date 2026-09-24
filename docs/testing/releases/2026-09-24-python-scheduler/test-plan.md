# Python scheduler, `.env` credentials and script monitoring: test plan

- Change/PR: [PR #143](https://github.com/datap0nd/data_governance/pull/143),
  delivered in one go at the owner's request — the plan set in
  `docs/plans/python_scheduler/` plus its three parts: Flow SQL credentials
  from `.env`, "Just run it" Python Flows, and script monitoring with run
  history.
- Code baseline: `main` `a3a3b62` (PR #142 merged).
- Related report: [test-report.md](test-report.md)
- Intended environments: WSL Ubuntu with the checkout-owned Python 3.13
  `.venv`, Windows Node.js for UI contracts, the fictional browser preview,
  and required GitHub Actions CI.

## Prerequisites and test data

Use isolated verifier fixtures, disposable synthetic `.py` scripts and a local
HTTP server for the fictional preview. The verifier owns the database and Flow
paths. Node.js runs the UI contracts. The preview API is in memory.

## Test cases

### Plan documents (checked at the plan-only head)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| D-01 | `git diff --check origin/main...HEAD` | No whitespace errors | Command output |
| D-02 | Resolve every relative Markdown link in `docs/plans/python_scheduler/*.md`, this package, `docs/testing/releases/INDEX.md` and `docs/testing/README.md` (script in the report) | Every relative link points to an existing file | Script output |
| D-03 | Confirm every repository path the plans name as existing (for example `app/config.py`, `app/flow_python.py`, `app/flow_recorder_worker.py`, `setup.ps1`, `tests/test_auditor_managed.py`) exists at the baseline | All named existing paths exist; planned new files are named as new | Script output |
| D-04 | Final-head CI | `Merge ready` passes on the final head (a plan-only head is classified documentation-only) | Run URL and head SHA in the PR testing section |

### Implementation

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| E-01 | Run `tests/test_env_file.py`: BOM, export, quotes, `#` in values, invalid lines, precedence, empty override, missing/unreadable file | Values load before config; diagnostics have names and line numbers only; missing files do not stop startup | Verifier result and test assertions |
| E-02 | Run portable config and standalone tests with a synthetic parent `.env` and `DG_ENV_FILE=""` | Portable launcher picks the nearest file unless disabled and dry-run imports successfully | Verifier result |
| E-03 | Inspect `.env.example`, `.gitignore`, `setup.ps1`; run auditor managed contracts | Empty credentials template, ignored `.env`, create-only installer with separate ACL grants and auditor deny | Tests and source review |
| R-01 | Save a run-mode Python Flow with prior SQL/email/XLSX settings; inspect job; queue it for old/new workers | File/SQL/email controls are inert, saved mode round-trips, only a worker with `python_script_run_v1` claims | `tests/test_flow_run_mode.py` |
| R-02 | Run a synthetic script with a sibling import, quoted arguments, values, stale output variables and `input()` | Exact argv, script-folder working directory, run environment, closed stdin, no output artifact | `tests/test_flow_run_mode.py` |
| R-03 | Run a parent that starts a child then exits; run a nonzero script; time out a parent and child | Child is awaited and its output captured; nonzero exit has a step record and tail; timeout ends the tree | `tests/test_flow_run_mode.py` |
| R-04 | Run existing Python file-producing and standalone suites | `--input`/`--output`, file validation and portable dry-run continue to work | Verifier result |
| M-01 | Post assigned live lines and process snapshots twice, then read incrementally; post 6000 lines | Wrong worker rejected, duplicate lines ignored, parsed live state returned, first 500/latest 4500 kept with omission count | `tests/test_flow_live_output.py` |
| M-02 | Feed stage/progress markers and a secret value to the observer | Stage/progress parsed, emitted event, value masked before posting | `tests/test_flow_live_output.py` |
| M-03 | Stop an assigned run-mode job and post its final snapshot twice | Worker stays assigned for cooperative cleanup, one final snapshot accepted, `python_stopped` recorded | `tests/test_flow_live_output.py` |
| M-04 | Run Node UI contracts for the builder and incremental console | Run-mode payload correct; console escapes text, marks stderr/stages, shows omissions and honors Follow | `.mjs` command output |
| U-01 | At 1280×900, create a run-mode Flow, check a script, advance steps, trigger a save validation error, then recover | Two visible steps, inspection status, preserved fields and successful retry | Fictional preview walkthrough |
| U-02 | At 1280×900, filter history by status, open script log and choose Stop then failure | Filter narrows rows; stage, child processes, console and clear Stop/failure messages appear | Fictional preview walkthrough |
| U-03 | At 390×844, inspect run-mode steps and switch to file-producing mode | No page-wide horizontal overflow; run mode has 2 steps; file mode restores Output and Email | Fictional preview walkthrough |

## Automated checks

Documentation checks D-01 to D-03 at the plan-only head. For the
implementation, the smallest affected `python tools/check.py verify` set per
part, `node tests/test_flow_builder_contract.mjs` and the new `.mjs` tests,
`node --check` on changed JavaScript, and `git diff --check`; required
final-head CI (D-04) is the full regression.

## Usability evidence

The fictional preview `app/static/recording-preview/python-scheduler.html`,
walked by Playwright at 1280×900 and 390×844. The owner waived the stop for
preview feedback ("I want it done in one go please"), so the walkthrough is
evidence, not an approval gate.

## Acceptance and cleanup

Accepted when every in-scope case has a recorded result, the final-head
`Merge ready` passes with its run URL and SHA in the PR, and the PR merges
pinned to that head. Test databases, Flow roots and synthetic scripts are
per-run fixtures removed by the verifier on success. Rollback: see the plan
README.
