# Opt-in live testing and post-PR #92 scope review: test plan

- Change/PR: make work-PC/live portal testing explicitly opt-in and remove redundant local and post-merge regression work
- Code baseline: `ceee3c1c9f07b9192e08916cc894fada2b342166`
- Related report: [test-report.md](test-report.md)
- Intended environments: local Windows policy/workflow inspection and GitHub-hosted CI

## Prerequisites and test data

- Start from current `origin/main` without application or production-data changes.
- Use repository files and GitHub Actions metadata only.
- Do not open, inspect or test the work PC, live portal, authentication or hardware.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| POL-01 | Inspect `AGENTS.md`, `CLAUDE.md`, the PR template, testing workflow guide and templates. | Each makes live/work-PC testing opt-in, forbids invoking a Metronome/live-fix skill for routine testing and tells contributors to omit unrequested live placeholders. | Diff and text assertions |
| POL-02 | Inspect local-test guidance in `AGENTS.md`, `CLAUDE.md` and the testing guide. | It calls for one non-overlapping affected set; final-head CI owns the full regression; docs-only rebases do not trigger repeated application suites. | Diff and text assertions |
| CI-01 | Parse `.github/workflows/tests.yml` and inspect change classification and the event-dependent matrix. | Docs, policy, PR-template and workflow-only PRs use the lightweight scope gate; other PRs run Ubuntu; push to `main` runs Windows; nightly/manual runs Ubuntu and Windows. | YAML parse and source assertions |
| CI-02 | Inspect concurrency and test commands. | A newer PR commit or main merge cancels the obsolete same-ref run; scheduled/manual runs remain independent; application suites depend on the scope result; the Python suite reports its 20 slowest cases. | YAML/source assertions |
| CI-03 | Compare PR #91/#92 run timings. | Report uses actual hosted timestamps and distinguishes PR wall time, frontend time and the Python bottleneck. | GitHub Actions metadata |
| CI-04 | Wait for the final PR head. | The lightweight scope gate and deployment checks pass; Python/frontend are skipped because this PR changes only policy, documentation, templates and workflow orchestration. | PR run URL and exact head SHA |
| CI-05 | After merge, inspect the `main` workflow. | The newest main run creates Windows Python but not duplicate Ubuntu Python; any older in-progress main run is superseded. | Post-merge run URL; not pre-merge evidence |

## Automated checks

```powershell
python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml', encoding='utf-8'))"
git diff --check
```

Run focused text assertions for the policy, scope allowlist, event matrix,
cancellation expression, duration reporting and PR-template wording.
Application tests are intentionally not run because no application, dependency
or test code changes.

## Acceptance and cleanup

Accept when the policy is unambiguously opt-in, local guidance prevents
overlapping suites, workflow structure passes focused checks and final PR CI is
green. No application data, portal session, live environment or cleanup is in
scope. Rollback is a commit revert.
