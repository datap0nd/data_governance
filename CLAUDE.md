# Claude instructions for this repository

## Delivery workflow (standing instruction from the repo owner)

Every implementation request is only done once it is **merged into GitHub
`main`**. The owner updates the running Metronome app from `main` and tests
there, so work left on a branch or an unmerged PR does not exist for them.

For any request that changes code:

1. Implement on the session's designated `claude/...` branch (restart it from
   the latest `origin/main` if its previous PR was already merged).
2. Validate through `tools/check.ps1` before pushing with one smallest
   non-overlapping affected test set
   and applicable syntax checks. Do not repeat that set inside a broader local
   suite. Final-head CI is the authoritative full regression for application,
   dependency and test changes; documentation, repository-policy, PR-template
   and workflow-only changes use the lightweight CI scope gate.
3. Commit, push, and open a PR to `main` once focused evidence is ready. Wait
   for the required final-head `Merge ready` check, add its URL and tested SHA
   to the PR, then perform a head-pinned merge immediately; standing owner
   authorization covers that merge.
4. In the reply, state clearly that the change is merged to `main`, so the
   owner knows it is safe to update the app. If for any reason the merge did
   not happen (failing tests, conflict, denied permission), say so explicitly
   at the top of the reply — never leave the impression that unmerged work is
   available.

Pure questions, analysis, or advice requests do not trigger this workflow —
only actual code changes do.

## Testing documentation on every main merge (standing owner instruction)

Every PR merged to `main`, including documentation and maintenance, must include
or update a release-specific test plan and test report, and link both in its PR
and delivery reply. Follow [AGENTS.md](AGENTS.md) and the
[testing workflow](docs/testing/README.md). Include concrete testing instructions,
actual automated results with revision/environment/evidence. Work-PC, portal,
authentication and hardware checks are out of scope unless the owner explicitly
requests them in the current task; omit them rather than recording placeholders.
Never invoke a Metronome/live-fix skill for testing unless the owner explicitly
requests that skill in the current task.
Wait for the final PR checks before merging and record that CI run and tested
SHA in the PR. Identify synthetic tests accurately and do not publish private
data.

## Supported local verification

Create or refresh the checkout-owned Python 3.13 environment with
`tools/check.ps1 -Mode Setup`. Use `-Mode Preflight` for one actionable
environment diagnosis. Routine verification requires explicit `-TestPath`
selectors and applicable `-SyntaxPath` values; `-Full` requires a recorded
`-DiagnosticReason`. Do not search for or borrow sibling, temporary or bundled
agent environments. The command isolates databases, temporary files, locks,
browser profiles and evidence under an ignored per-run directory and records a
machine-readable result. Reuse evidence only with `-Reuse` when its fingerprint
still matches.

CI scope selection comes from `tools/ci/change_scope.py` and
`ci/windows-sensitive-paths.txt`; unknown backend paths default to Windows.
Selected Python suites are six deterministic file-level shards per OS. The
inventory reconciliation and `Merge ready` results are mandatory evidence, and
CI-orchestration changes require same-head serial outcome equivalence.
