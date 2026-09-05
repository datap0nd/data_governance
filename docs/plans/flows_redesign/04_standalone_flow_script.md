# Plan 4 — Standalone installed-code launcher

## Goal
Scripts/run_flow.py and frozen configuration run with the server unavailable;
installed code, Python/dependencies, credentials and portal access remain required.

## Current state
execute_job(page, job, progress, profile_dir, staging, artifacts, *, run_id,
register_folder, headed) downloads. run_worker creates the browser, publishes,
transforms and loads SQL. _build_job depends on router source/SQL helpers.

## Design
- Tiny launcher uses safely encoded constants, installed Python and an atomic
  versioned bundle. Freeze config, never cookies/credentials/service environment.
- Extract one execute_flow orchestration path used by both the Metronome worker
  and launcher: same acquisition, publication, transformation, SQL, artifact roles
  and no-op handling. Metronome wraps it with scheduling and server history.
- Explicit local DB refresh may adapt _build_job; do not claim router isolation
  by merely moving week helpers. Explain frozen latest-period windows.
- Dedicated locked standalone browser profile; no live-profile copying/lock theft.
- Unique standalone IDs outside server sequence, local logs, no retention deletion,
  no server history/receipt mutations and no API calls.
- Revalidate current root/layout. Both workers and launcher need shared OS locks
  for flow/output/SQL resources, because API reservations cannot guard offline work.
- User clarification: all saved stages, including SQL, run by default. Use the
  same installed credential configuration as Metronome; --no-sql/--no-transform
  are explicit overrides. Never embed credentials or generate secret files.
- Honor saved browser mode; support --headed/--headless and a dry-run that
  reads only the bundle, creates nothing and prints a redacted summary.
- Regenerate on managed save/adopt/repair or explicit request; deterministic
  config hashes and current/stale/missing status. Avoid startup network sweeps.

## Step-by-step
Shared execution locks → bundle/CLI → browser/source/finalization lifecycle →
save/status/regenerate endpoints and row actions → operator guide.

## Risks
Offline execution cannot prove exactly-once append SQL after an unknown commit.
A dedicated profile may require initial interactive SSO.

## Acceptance criteria
Adversarial launcher paths compile; dry-run creates nothing; fake source dispatch
never calls API; correct publication/transform inputs; partial artifacts survive;
competing locks refuse; bundles contain no secrets. Live SSO/SQL is a separate check.

