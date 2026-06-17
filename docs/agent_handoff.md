# Agent Handoff

## Current Objective
Keep the Import Data workflow stable while splitting the focused import-and-schedule experience into a standalone private platform named Cadence.

## Repo State
- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest commit before this handoff: `99031ec Restore Metronome branding`
- Public repo: no, GitHub reports `datap0nd/data_governance` as `PRIVATE`.
- Push status: Metronome branding restoration is committed and pushed; this handoff update is pending commit and push.

## Decisions Made
- The top-right app branding should remain `Metronome` in this repo. The prior `Data Governance` label came from commit `5994f09`, where branding was over-generalized during a privacy pass without first checking repo visibility.
- The privacy pass was unnecessary for this private repo. Keep secrets and internal paths out of commits, but do not rename the product away from Metronome for privacy reasons here.
- The standalone import platform name is `Cadence`, chosen as a musical sibling to Metronome and as a fit for scheduled, repeatable data movement.
- Cadence now lives in a separate private repo: `https://github.com/datap0nd/cadence`.
- Cadence is focused on CSV/Excel to PostgreSQL imports, one-time table creation, recurring append or truncate-and-replace scripts, selected materialized-view refreshes, and UI-defined Prefect schedules.

## Files Changed
- `app/static/index.html`: restored Metronome title/brand text and cache-busted static assets.
- `app/static/app.js`: restored Metronome user-facing text in notifications and welcome copy.
- `app/static/style.css`: restored Metronome logo selector names/comments.
- `docs/agent_handoff.md`: updates durable context after the branding fix and Cadence repo split.

## Commands And Checks
- `gh repo view datap0nd/data_governance --json nameWithOwner,visibility,url`: confirmed private repo.
- `git log --oneline`: confirmed the branding regression came from `5994f09 Split table creation from import scheduling`.
- Bundled Node `--check app/static/app.js`: passed for the branding restoration.
- `git diff --check`: passed before the branding restoration commit.
- `git push origin main`: pushed `99031ec Restore Metronome branding`.
- Cadence checks in `/Users/rafaelcunha/Documents/cadence`: JS syntax passed, Python syntax passed, privacy scan passed, generated Prefect script compile check passed, browser UI harness passed, private repo creation and push passed.
- Not run: live PostgreSQL imports/materialized-view refreshes or live Prefect deployment serving, because no configured local target PostgreSQL connection or Prefect server was available in this shell.
- Not run: `setup.ps1` PowerShell parse check for Cadence, because `pwsh` is not installed on this Mac.

## Open Questions
- Confirm on the Windows deployment machine that the Cadence `setup.ps1` installs and updates the private repo correctly with `CADENCE_GITHUB_TOKEN`.
- Confirm with a non-critical PostgreSQL table that scheduled Cadence scripts can append, truncate-and-replace, and refresh selected materialized views without exhausting database connection slots.
- Decide whether Metronome should link out to Cadence after the split, or whether the import workflow should eventually be removed from Metronome entirely.

## Next Step
Install Cadence on the Windows deployment machine with a read-capable GitHub token, then generate and serve one scheduled Prefect import against a non-critical table.
