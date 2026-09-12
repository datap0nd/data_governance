# Agent guidance refresh and cross-platform verifier: test plan

- Change/PR: `AGENTS.md` rewritten as the single canonical working guide (done criteria, precedence, invariants, codebase map, conditional reading, verification, replies); `CLAUDE.md` reduced to an import plus Claude-specific notes; new `tools/check.py`, the Python counterpart of `tools/check.ps1` for Linux, macOS and containers; a Claude Code SessionStart hook that builds the checkout-owned `.venv`; stale root documents moved to `docs/archive/`; `README.md` corrected (product name, Python 3.13, `.venv`, tests, services and environment variables); `docs/testing/README.md` slimmed with the release index moved to `docs/testing/releases/INDEX.md`; `DESIGN.md` review pause narrowed to material journey changes; documentation-scope allowlist in CI extended.
- Code baseline: `37169ef` (`main` at PR #117).
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container with Python 3.13 and Node 22 (this task); GitHub-hosted Ubuntu CI for the final regression. No live environment.

## Prerequisites and test data

A clean checkout. The verifier creates `.venv` from `requirements-ci.lock` and
writes every artifact under an ignored `.test-runs/<run id>/` directory and a
per-run Flow root outside the checkout. No production path, database or
credential is an input.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| AG-01 | Run `python tools/check.py setup`, then `python tools/check.py preflight`. | `.venv` uses Python 3.13, `.venv/.metronome-ci-lock.sha256` matches the lock, required modules import, and each run writes `result.json` with revision and duration. | Console and `result.json` summaries. |
| AG-02 | Run `verify` without selectors; run `verify --full` without `--diagnostic-reason`. | Each stops before pytest with exit code 2, an actionable message on stderr and a `result.json` whose `selection` records the request. | `tests/test_check_python_command.py`. |
| AG-03 | Run `verify --test tests/test_check_python_command.py --test tests/test_check_command.py --test tests/test_ci_merge_gate.py --syntax tools/check.py --syntax tools/ci/merge_gate.py --syntax app/static/app.js`. | Syntax checks pass, the selection passes, the PowerShell-only cases skip off Windows, and `test_summary` in `result.json` carries the counts. | `result.json` and JUnit under `.test-runs/`. |
| AG-04 | Repeat AG-03 with `--reuse`. | The run finishes immediately as `passed` with `reused_from` pointing at the AG-03 result and the diagnostic "Reused unchanged successful local evidence." | `result.json`. |
| AG-05 | Unit-level: isolation environment, cleanup guard, reuse matching, interpreter checks, JUnit aggregation and source fingerprint. | Every path is under the run root or the external isolation base; cleanup refuses roots outside that base; reuse requires status, fingerprint and selection to match; a bundled `codex-runtimes` interpreter is rejected; the fingerprint changes with source or selection. | `tests/test_check_python_command.py`. |
| AG-06 | Run `CLAUDE_CODE_REMOTE=true ./.claude/hooks/session-start.sh` twice. | First run builds or verifies `.venv`; second run only runs preflight; both exit 0. Without `CLAUDE_CODE_REMOTE` the hook exits 0 immediately. | Console. |
| AG-07 | Documentation: parse `.github/workflows/tests.yml`, validate `.claude/settings.json`, `bash -n` the hook, `git diff --check`, and resolve every relative link in the touched markdown files. | All pass; no broken links; the workflow's documentation allowlist includes `DESIGN.md`, `PRODUCT.md` and `.claude/*`. | Console. |
| AG-08 | Read `AGENTS.md`, `CLAUDE.md`, `docs/testing/README.md`, `docs/archive/README.md` and `README.md` for content checks. | `AGENTS.md` is the only file holding the working rules; `CLAUDE.md` starts with `@AGENTS.md`; the testing README links the index and both verifiers; the archive README lists every moved file with a reason; `README.md` names Metronome, Python 3.13, the verifier and the archived plan. | Reviewer reading. |

## Automated checks

Linux (this task), through the new verifier:

```bash
python tools/check.py setup
python tools/check.py preflight
python tools/check.py verify \
  --test tests/test_check_python_command.py \
  --test tests/test_check_command.py \
  --test tests/test_ci_merge_gate.py \
  --syntax tools/check.py --syntax tools/ci/merge_gate.py --syntax app/static/app.js
python tools/check.py verify --reuse <same selection>
CLAUDE_CODE_REMOTE=true ./.claude/hooks/session-start.sh
python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"
git diff --check
```

On Windows the equivalent is `.\tools\check.ps1 -Mode Verify -TestPath tests/test_check_python_command.py,tests/test_check_command.py,tests/test_ci_merge_gate.py -SyntaxPath tools/check.py,tools/ci/merge_gate.py,app/static/app.js`, which also exercises the PowerShell-only cases. Because `tools/` and `tests/` change, final-head CI runs the full Python and frontend suites; the full suite is not repeated locally.

## Acceptance and cleanup

Accept when AG-01 to AG-07 pass on the tested revision, AG-08 holds on review, and final-head `Merge ready` is green and recorded in the PR. Cleanup: `.venv/`, `.test-runs/` and the external `MetronomeTestRuns/<run id>` directories are disposable. Rollback is a normal `main` revert; the archived documents are moves and can be restored with `git mv`.
