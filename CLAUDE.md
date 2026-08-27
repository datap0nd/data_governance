# Claude instructions for this repository

## Delivery workflow (standing instruction from the repo owner)

Every implementation request is only done once it is **merged into GitHub
`main`**. The owner updates the running Metronome app from `main` and tests
there, so work left on a branch or an unmerged PR does not exist for them.

For any request that changes code:

1. Implement on the session's designated `claude/...` branch (restart it from
   the latest `origin/main` if its previous PR was already merged).
2. Validate before pushing: run the affected Python tests (full suite when the
   change is not trivially isolated) and `node --check app/static/app.js` for
   frontend changes.
3. Commit, push, open a PR to `main`, and **merge it immediately** — do not
   wait for the owner to say "merge to main"; that approval is standing.
4. In the reply, state clearly that the change is merged to `main`, so the
   owner knows it is safe to update the app. If for any reason the merge did
   not happen (failing tests, conflict, denied permission), say so explicitly
   at the top of the reply — never leave the impression that unmerged work is
   available.

Pure questions, analysis, or advice requests do not trigger this workflow —
only actual code changes do.
