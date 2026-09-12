# 2026-09-12 agent guidance refresh and cross-platform verifier: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: agent guidance rewrite, `tools/check.py`, SessionStart bootstrap,
  archive of stale root documents
- Evidence cutoff (UTC): 2026-09-12 06:40
- Tested code revision: `b67c6dc` (`Modernize agent guidance and add a
  cross-platform verifier`). The report file itself is committed afterwards and
  changes no tested behavior.
- Environment: Linux agent container (no PowerShell), checkout-owned `.venv` on
  CPython 3.13.12, dependencies from `requirements-ci.lock`, Node 22 available
  but unused by this selection.
- Overall finding: PASS for every automated check in scope. Final-head CI is
  NOT RUN at this cutoff and is recorded in the PR testing section before merge.
  No live, work-PC or portal testing was requested, so none is reported.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-01 | `python tools/check.py setup` | `b67c6dc`, Linux, `.venv` deleted first | PASS — created `.venv` on Python 3.13.12, installed the 37 locked distributions, wrote `.venv/.metronome-ci-lock.sha256` | `.test-runs/20260912T064000532Z-2607-b09b4abe/result.json` |
| T-02 | `python tools/check.py preflight` | `b67c6dc`, Linux | PASS — Python 3.13.12, lock fingerprint matched, `pip check` clean, required modules importable; 0.316 s | `.test-runs/20260912T063942296Z-2557-401c4075/result.json` |
| T-03, T-04, T-05, T-06, T-07 | `python tools/check.py verify --test tests/test_check_command.py --test tests/test_ci_merge_gate.py --syntax tools/check.py --syntax tools/ci/merge_gate.py` | `b67c6dc`, Linux | PASS — 17 tests, 0 failures, 0 errors, 2 skipped (the two PowerShell execution tests, Windows-only), 0.877 s | `.test-runs/20260912T063942662Z-2562-e6b9d206/result.json` |
| T-07 | `python tools/check.py verify --test tests/test_ci_merge_gate.py --reuse` run twice | `b67c6dc`, Linux | PASS — first run executed 7 tests; the repeat reported `Reused unchanged successful local evidence.` with `reused_from` pointing at the first result | `.test-runs/20260912T063945624Z-2595-c57086cc/result.json` |
| T-08 | `CLAUDE_CODE_REMOTE=true CLAUDE_PROJECT_DIR=... .claude/hooks/session-start.sh`, then again with `.venv/.metronome-ci-lock.sha256` removed | `b67c6dc`, Linux | PASS — first run printed "`.venv` already matches requirements-ci.lock" and exited 0 without installing; second run re-ran setup and restored the marker | Shell transcript below |
| T-09 | `python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"` and `git diff --check` | `b67c6dc`, Linux | PASS — workflow parses with jobs `scope, python, frontend, postgres, merge-ready`; no whitespace errors | Command output |
| T-09b | Replayed the workflow's `case` classification over this PR's changed files | `b67c6dc`, Linux | PASS — `application=true` from `tools/check.py`, `tests/test_check_command.py` and `.github/workflows/tests.yml`; the archive moves and `.claude/` files classify as documentation scope | Command output |
| T-10 | Relative-link check over the 12 touched or newly linked markdown files | `b67c6dc`, Linux | PASS — 166 relative links resolve; the only failures before this file existed were the three pointers to this report | Script output |
| T-11 | `python tools/check.py verify --test tests/test_unattended_update_scripts.py --test tests/test_flows.py::test_setup_stops_headed_worker_before_replacing_runtime_code --test ...downloads_update_before_stopping_running_services --test ...waits_for_headless_worker_to_stop_before_replacing_code --test ...fails_closed_when_python_dependencies_cannot_install` | `b67c6dc`, Linux | PASS — 12 tests, 0 failures, 1 skipped (Windows scheduled updater), 1.298 s; confirms the root-level installer files the suite reads were not disturbed by the archive moves | `.test-runs/20260912T063943589Z-2578-c5b91346/result.json` |

T-08 transcript (paths shortened):

```
$ CLAUDE_CODE_REMOTE=true ./.claude/hooks/session-start.sh
Metronome: .venv already matches requirements-ci.lock.
$ mv .venv/.metronome-ci-lock.sha256 /tmp/marker.bak
$ CLAUDE_CODE_REMOTE=true ./.claude/hooks/session-start.sh
Metronome: creating or refreshing the checkout-owned .venv.
... Requirement already satisfied: ... (37 locked distributions)
Result: .test-runs/20260912T064012624Z-2673-66f05e55/result.json
```

The verifier also removed each run's external Flow root
(`~/.cache/MetronomeTestRuns/<run id>/`) on completion, leaving nothing behind.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Final-head CI (`Merge ready`, Python, frontend, PostgreSQL 14/18) | NOT RUN | The PR head had not finished CI at this evidence cutoff | Record the run URL and tested head SHA in the PR testing section, then merge pinned to that head |
| PowerShell execution paths of `tools/check.ps1` (2 skipped cases) and the Windows scheduled-updater case | NOT RUN | This session runs on Linux without PowerShell; those cases are Windows-only by design and unchanged by this PR | Windows CI covers `tests/` on `push` to `main`; the source-inspection cases for `check.ps1` ran here |

## Findings, limitations and retests

No failures and no retests. Two limitations worth stating:

- The PowerShell verifier's runtime behavior was not exercised here. What was
  verified on Linux is that its source still declares every isolated path before
  launching pytest and keeps the refusal guard around Flow-root cleanup — those
  cases no longer carry a Windows skip, so they now run in every session.
  `tools/check.py` was verified by execution.
- `setup_ps1_clean.txt` was **not** archived even though the change plan listed
  it as stale. Four cases in `tests/test_flows.py` and one in
  `tests/test_unattended_update_scripts.py` read it from the repository root and
  assert parity with `setup.ps1`, so it is a maintained mirror rather than
  history. T-11 records those cases passing unchanged.

All checks here are automated and synthetic; no browser, portal or work-PC
testing was requested or performed, and no report data appears in this package.

## Merge evidence

Final CI finishes after this file is committed, so it is pending here. Before
merging, the PR testing section records the final `Merge ready` run URL, the
tested head SHA and the result; the PR's merge record supplies the merge SHA.
This is pre-merge evidence, not post-deployment verification.
