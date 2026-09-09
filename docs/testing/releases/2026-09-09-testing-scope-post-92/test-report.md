# Opt-in live testing and post-PR #92 scope review: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-09T13:12:37Z; final-head CI pending
- Tested code revision: uncommitted policy/workflow changes based on `ceee3c1c9f07b9192e08916cc894fada2b342166`
- Environment: Windows 11 ARM64, Python 3.13.15; GitHub-hosted evidence from PRs #91 and #92
- Overall finding: focused policy, workflow, YAML, link and whitespace checks pass; final-head CI pending

## Review findings

| Finding | Evidence | Decision |
| --- | --- | --- |
| PR #92 substantially reduced the required PR wall time. | The old PR #91 head took 42m45s on Windows; after rebasing onto #92, the final PR run completed in 13m21s, with Ubuntu Python at 13m11s and frontend at 16s. | Keep Ubuntu full-suite plus parallel frontend as the final PR gate. |
| Python execution is now the PR bottleneck. | In run `34351643658`, dependency/browser setup took 45s and `pytest` took 12m15s; frontend completed in 16s. | Add `--durations=20` before considering sharding or selection changes. |
| Local verification duplicated coverage. | The recording change ran a 20-case focused set inside a later 94-case related set, then repeated focused cases after a docs-index-only rebase conflict, before full CI. | Require one non-overlapping affected local set and rely on final CI for the full regression. |
| Main pushes duplicate Ubuntu and queue long obsolete runs. | PR #92 already proved Ubuntu; its main run started Ubuntu and Windows, and PR #91's main run queued behind it. | Run Windows only on main pushes and cancel an older same-ref main run when a newer merge arrives; nightly/manual still run both OSes. |
| Default live placeholders create work without evidence. | Current policy/templates require NOT RUN/BLOCKED live entries even when no live check was requested. | Make work-PC, portal, authentication and hardware checks opt-in and omit them entirely by default. |
| A local Metronome live-fix skill could trigger unwanted live steps. | The owner explicitly requested its removal and prohibited routine testing use. | Remove the active local skill definition, scripts, control reference, configuration and signing key; prohibit invoking any replacement for testing unless explicitly requested. |
| Workflow-only PRs still triggered the full application suite. | PR #93 started all 1,882 Python cases even though it changed only policy/docs/templates/workflow orchestration; frontend finished in 16s while Python continued. | Cancel that run and add a native lightweight scope gate; application/dependency/test changes still receive the full suites. |

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| POL-01, POL-02 | PowerShell assertions across `AGENTS.md`, `CLAUDE.md`, PR/testing templates and the testing guide | Uncommitted tree based on `ceee3c1c`; Windows 11 ARM64 | PASS; opt-in, skill-prohibition, omission and non-overlap rules found; the default PR template has no live-testing field | Local console, 2026-09-09T12:59:15Z |
| CI-01, CI-02 | Parse `.github/workflows/tests.yml` with PyYAML; assert scope dependencies/allowlist, event matrix, main cancellation, duration reporting and synthetic-fixture label; classify the current diff | Same tree; Python 3.13.15 | PASS; current PR classified `application=False` | Local console, 2026-09-09T13:12:37Z |
| CI-03 | Inspect GitHub Actions metadata for runs `34347304196`, `34349388869`, `34350936870` and `34351643658` | GitHub-hosted runners | PASS; timings and job composition support the review findings | GitHub run metadata |
| Documentation | Verify new release files exist and run `git diff --check` | Same tree/environment | PASS; both files exist and no whitespace errors (line-ending notices only) | Local console, 2026-09-09T13:12:37Z |
| Superseded CI | Cancel run `34354407129` after confirming it unnecessarily started the full Python suite | PR #93 head `68eab31b` | CANCELLED intentionally; replacement head must exercise the scope gate | GitHub Actions |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| CI-04 | NOT RUN | Requires the committed final PR head. | Record the final run URL/SHA in the PR before merge. |
| CI-05 | NOT RUN | Requires merge to `main`. | Inspect the post-merge workflow without treating it as pre-merge evidence. |

## Findings, limitations and retests

The workflow retains the full Python suite on every application, dependency or
test PR and complementary Windows coverage on every latest main revision. Pure
docs/policy/template/workflow PRs use the scope gate. Both operating systems
still run nightly and on manual dispatch. Test selection or sharding is deliberately
deferred until duration output identifies stable targets; speculative parallel
execution could introduce shared-state failures and would increase compute.

The active local Metronome skill definition, source scripts, control reference,
configuration and signing key were removed. The host blocked deletion of two
compiled `__pycache__` files outside the workspace; without `SKILL.md` or source
they are inert and the skill is no longer discoverable or runnable.

## Merge evidence

Pending final-head CI and merge. The PR will preserve hosted results produced
after this report's committed cutoff.
