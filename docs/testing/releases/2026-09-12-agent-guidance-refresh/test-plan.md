# 2026-09-12 agent guidance refresh and cross-platform verifier: test plan

- Change/PR: modernize the agent guidance (`AGENTS.md`, `CLAUDE.md`, testing
  docs, README), add `tools/check.py` as a supported Linux/macOS/Windows
  verifier, add a SessionStart bootstrap and archive stale root documents.
- Code baseline: `37169ef` (`origin/main` at branch creation); no deployed app
  or worker behavior changes in this release.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux agent container, Python 3.13.12 in the
  checkout-owned `.venv`, plus required GitHub Actions CI. No live environment
  is in scope.

## Prerequisites and test data

No fixtures or credentials. `python tools/check.py setup` builds the
checkout-owned `.venv` from `requirements-ci.lock`; every verify run isolates
its database, temporary, browser-profile and Flow paths under
`.test-runs/<run id>/` and a cache-scoped `MetronomeTestRuns/<run id>/`.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | `python tools/check.py setup` in a checkout with no `.venv` | Creates `.venv` on Python 3.13, installs the exact lock, writes `.venv/.metronome-ci-lock.sha256`, `result.json` status `passed` | `result.json` run id |
| T-02 | `python tools/check.py preflight` | Reports Python 3.13, matching lock fingerprint, `pip check` clean and required modules importable; status `passed` | `result.json` run id |
| T-03 | `python tools/check.py verify --test tests/test_check_command.py --test tests/test_ci_merge_gate.py --syntax tools/check.py --syntax tools/ci/merge_gate.py` | Focused suite passes; `result.json` status `passed` with `test_summary` counts and a JUnit artifact | `result.json` run id, pytest summary |
| T-04 | `python tools/check.py verify` with no selector | Exits 2 with "Verify requires explicit --test selectors" and writes a `failed` result | Covered by `tests/test_check_command.py` |
| T-05 | `python tools/check.py verify --full` without a reason | Exits 2 with "A local full suite is diagnostic-only"; `selection.full_suite` is true | Covered by `tests/test_check_command.py` |
| T-06 | Inspect `tools/check.py` source ordering | Every isolated path (`TEMP`, `TMP`, `TMPDIR`, `DG_DB_PATH`, `DG_TEST_RUN_ROOT`, `DG_BROWSER_PROFILE_ROOT`, `DG_FLOWS_ROOT`, `PLAYWRIGHT_BROWSERS_PATH`) is assigned before the pytest launch, and the Flow-root cleanup keeps its guard | Covered by `tests/test_check_command.py` |
| T-07 | `--reuse` matching on fingerprint and selection | A prior passing result is reused only when both its `source_fingerprint` and `selection` match | Covered by `tests/test_check_command.py` |
| T-08 | `CLAUDE_CODE_REMOTE=true .claude/hooks/session-start.sh` with a current `.venv`, then with the lock marker removed | First run reports the environment already matches and exits 0 without installing; second run re-runs setup and restores the marker | Shell transcript in the report |
| T-09 | `python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"` and `git diff --check` | Workflow parses; no whitespace errors in the diff | Command output |
| T-10 | Relative-link check over `AGENTS.md`, `CLAUDE.md`, `README.md`, `DESIGN.md`, `PRODUCT.md`, `docs/testing/README.md`, `docs/testing/releases/INDEX.md`, `docs/archive/README.md`, `docs/production_hardening_plan.md` | Every relative link resolves to an existing path after the archive moves | Script output |
| T-11 | `python tools/check.py verify --test tests/test_flows.py -k setup --syntax tools/check.ps1` is out of scope here; instead run the tests that read root-level installer files: `tests/test_unattended_update_scripts.py` and the `setup_ps1_clean.txt` cases in `tests/test_flows.py` | Pass, proving the archive moves did not disturb files the suite reads from the repository root | `result.json` run id |

Negative and recovery paths covered: missing selector (T-04), unreasoned full
suite (T-05), stale environment fingerprint (T-08 second half), and the refusal
guard around external Flow-root cleanup (T-06).

## Automated checks

Local: `tools/check.py` as listed above, run from the checkout-owned `.venv`
(Python 3.13.12). Required CI on the final head is the authoritative full
regression: this PR changes `tools/`, `tests/` and a workflow, so the Python,
frontend and PostgreSQL jobs are all selected and gated by `Merge ready`.

## Usability evidence

Omitted: no user interface changed.

## Acceptance and cleanup

Accepted when the focused local sets pass, the final-head `Merge ready` check is
green with its run URL and SHA recorded in the PR, and the guidance files
resolve their links. No cleanup is needed: `.test-runs/` and `.venv/` are
ignored, and the per-run Flow root is removed by the verifier itself. Rollback
is a revert of the PR; no data migration or deployed behavior is involved.
