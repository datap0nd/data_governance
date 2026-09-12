# Agent guidance refresh and cross-platform verifier: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: agent guidance refresh, `tools/check.py`, SessionStart hook and document archive (PR link recorded in the merge evidence below).
- Evidence cutoff (UTC): 2026-09-12T06:47Z, before final-head GitHub CI.
- Tested code revision: baseline `37169efe0cb33eb675224798fc9a1a33fde052b7` plus this PR's uncommitted diff at the time of the runs; the PR head SHA is recorded in the PR testing section.
- Environment: Linux container (`posix linux`), Python 3.13.12 in the checkout-owned `.venv` created by `python tools/check.py setup` from `requirements-ci.lock` (pytest 9.1.1, Playwright 1.62.0), Node 22.22.2, git shallow clone. No PowerShell, so the PowerShell-only cases in `tests/test_check_command.py` skip here and run in Windows CI on `main` pushes.
- Overall finding: synthetic checks pass. The Python verifier reproduces the PowerShell verifier's rules, isolation and result schema on Linux; the session hook builds and verifies the environment; the documentation set has no broken links and the workflow still parses.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| AG-01 | `python3.13 tools/check.py setup`; `python3.13 tools/check.py preflight` | Uncommitted diff on `37169ef`, Linux | PASS. Setup installed the 37 locked packages and wrote `.venv/.metronome-ci-lock.sha256`; preflight reported `Python 3.13.12, locked dependencies` and `Dependency imports: ready`. | `.test-runs/20260912T064226613Z-856-72ec09e6/result.json`, `.test-runs/20260912T064258614Z-933-83fe8515/result.json` (ignored local files) |
| AG-02, AG-03, AG-05 first run | `python3.13 tools/check.py verify --test tests/test_check_python_command.py --test tests/test_ci_merge_gate.py --test tests/test_check_command.py --syntax tools/check.py --syntax tools/ci/merge_gate.py` | Same | 1 failed, 17 passed, 3 skipped in 0.29s. The failure was in the new verifier: `result.json` did not record `selection.full_suite` when validation rejected the request, unlike `check.ps1`. Fixed by recording the selection before validation. | Local run output |
| Retest | `python3.13 tools/check.py verify --test "tests/test_check_python_command.py::test_full_suite_requires_a_recorded_diagnostic_reason" --test "tests/test_check_python_command.py::test_verify_requires_an_explicit_focused_selection" --syntax tools/check.py` | Fixed diff | PASS: 2 passed in 0.16s. | `.test-runs/20260912T064313568Z-980-c3b80985/result.json` |
| AG-02, AG-03, AG-05 final | `python3.13 tools/check.py verify --test tests/test_check_python_command.py --test tests/test_check_command.py --test tests/test_ci_merge_gate.py --syntax tools/check.py --syntax tools/ci/merge_gate.py --syntax app/static/app.js` | Final diff | PASS: 18 passed, 3 skipped (PowerShell-only cases) in 0.27s; `test_summary` = 21 tests, 0 failures, 0 errors, 3 skipped; three syntax checks passed; run duration 1.355s. | `.test-runs/20260912T064557521Z-1106-24018163/result.json`, JUnit `pytest.xml` beside it |
| AG-04 | Same selection with `--reuse` | Final diff | PASS: status `passed`, `reused_from` names the AG-03 result, diagnostic "Reused unchanged successful local evidence."; pytest was not launched. | `.test-runs/20260912T064612928Z-1140-27bf1e30/result.json` |
| AG-06 | `CLAUDE_CODE_REMOTE=true CLAUDE_PROJECT_DIR=$PWD ./.claude/hooks/session-start.sh` | Final diff | PASS: with the lock marker already matching, the hook ran preflight only and exited 0. | Local run output |
| AG-07 | `python3 -c "import yaml; yaml.safe_load(...)"`, `python3 -c "import json; json.load(...)"`, `bash -n .claude/hooks/session-start.sh`, `.venv/bin/python -m py_compile tools/check.py tests/test_check_python_command.py`, `git diff --check`, relative-link script over the ten touched markdown files | Final diff | PASS: workflow parsed with jobs scope, python, frontend, postgres, merge-ready; JSON and shell valid; no whitespace errors; broken links: none. | Local run output |
| AG-08 | Reviewer reading of `AGENTS.md`, `CLAUDE.md`, `docs/testing/README.md`, `docs/testing/releases/INDEX.md`, `docs/archive/README.md`, `README.md` | Final diff | PASS on review: rules live only in `AGENTS.md`; `CLAUDE.md` starts with `@AGENTS.md`; the index carries all 46 prior rows plus this package; the archive README lists all six moved files with reasons. | This PR's diff |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| `tests/test_check_command.py` (PowerShell verifier cases) | NOT RUN locally | No PowerShell in this container; the cases skip off Windows by design. | Windows CI on the `main` push runs them; final-head Ubuntu CI runs the rest of the suite. |
| Full Python and frontend regression | NOT RUN locally | Reserved for final-head CI per the verification rule. | Record the `Merge ready` run URL and head SHA in the PR before merging. |

## Findings, limitations and retests

- The one local failure was a fidelity gap in the new Python verifier (selection not recorded on rejected requests); the fix and its retest are above.
- `PLAYWRIGHT_BROWSERS_PATH` is respected when preset (this container installs Chromium under `/opt/pw-browsers`); otherwise the verifier points at the checkout-local `.playwright-browsers` folder like `check.ps1`. No browser test was part of this selection.
- The source fingerprint hashes tracked `app/` and `tools/` files plus the selection in the same shape as the PowerShell version, but results are not interchangeable between the two verifiers because sort order differs; reuse works within one verifier.
- The document moves are `git mv` renames; inbound links were updated (`README.md` roadmap line) and the link check found no remaining references to the old paths outside historical release packages.

## Merge evidence

Final-head CI: PENDING at this cutoff. Before the head-pinned merge, the PR testing section records the `Merge ready` run URL and the tested head SHA; the PR supplies the merge record.
