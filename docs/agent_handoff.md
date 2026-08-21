# Agent Handoff

## Current Objective

Make each successful GSCM bookmark scan replace the prior discovered bookmark
snapshot instead of accumulating stale scan rows.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest base commit: `c83846b`
- Public repo: no, private
- Delivery target: commit and push the scoped GSCM snapshot change to
  `origin/main`
- Preserve untracked `governance.db-shm` and `governance.db-wal`

## Decisions Made

- Treat every successful, complete, non-empty GSCM scan as an authoritative
  bookmark snapshot.
- Before applying that snapshot, delete prior unreferenced discovered GSCM
  reports and their discovered filters. Current bookmarks are then inserted
  from the new result rather than merged with old scan residue.
- Preserve historical operation timing rows but clear their report reference
  before deleting a disposable report.
- A missing bookmark referenced by an existing Flow must remain as a stale,
  disabled tombstone so the Flow's foreign key is not broken.
- Failed, cancelled, empty, or validation-incomplete GSCM scans do not replace
  the last good catalog.
- Keep ASAP discovery behavior unchanged.

## Files Changed

- `app/routers/flows.py`: GSCM snapshot reset, referential-integrity guard, and
  incomplete-snapshot protection.
- `tests/test_gscm.py`: replacement, reference preservation, timing detachment,
  empty-snapshot, and rejected-bookmark coverage.
- `docs/gscm_portal.md`: document authoritative scan replacement behavior.
- `docs/agent_handoff.md`: this handoff.

## Commands And Checks

- `python -m pytest tests/test_gscm.py -q`: 63 passed.
- `python -m pytest tests/test_flows.py -q`: 129 passed.
- `python -m pytest -q`: 408 passed.
- `python -m compileall -q app`: passed.
- `git diff --check`: passed.
- Not run: an authenticated live GSCM scan on the BI desktop.

## Open Questions

- Confirm a live second GSCM scan reports its reset count and leaves only the
  current active bookmark snapshot plus any Flow-referenced stale tombstones.
- The catalog scan cancellation change still needs one live Windows worker
  validation.
- The ASAP dashboard download change still needs one explicitly authorized
  production Flow run for end-to-end validation.

## Next Step

After installing the delivered commit on the BI desktop, run two consecutive
authenticated GSCM bookmark scans and compare active bookmark count and ids.
Confirm the second scan contains no unreferenced residue from the first and
that an existing GSCM Flow still resolves its bookmark.
