# Agent Handoff

## Current Objective

Live-verify hover-safe week selection and normalized append-column mapping on the
nine-download ASAP flow, then verify Stop with two queued flows.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest runtime commit: `c0a6b8a Map SQL append columns safely`
- Quoted-target runtime commit: `2fcad3a Support quoted SQL handoff targets`
- Week-selection runtime commit: `c6da87f Reject hovered ASAP week selections`
- Push status: runtime commit verified on `origin/main`
- Public repo: no, private
- Stable baseline: tag `asap-ui-automation-stable-2026-08-14` at `d2b61f1`
- Preserve untracked `governance.db-shm` and `governance.db-wal`; never stage them

## Decisions Made

- The unverified REST rewrite remains reverted. Runtime behavior uses the UI
  scraper restored from the stable baseline.
- Every scraper member selection is one ordinary left click with no keyboard
  modifier. The week and Dimension path explicitly uses one 100 ms left-button
  press-and-release.
- ASAP uses a blue background for both selected and hovered rows. The scraper
  now parks the pointer away from the prompt before reading state and refuses
  to use blue styling as selection evidence while a row or ancestor is hovered.
- A click is not successful until the non-hovered rendered state confirms it.
  Unknown state counts as missing, retries are bounded, and the exact requested
  set must remain stable across three reads before RUN is allowed.
- Dimension clearing reconciles across four rounds and uses the same hover-safe
  click confirmation.
- SQL handoff schema and table names come from the discovered PostgreSQL catalog.
  They may contain spaces or punctuation and are now safely double-quoted, with
  embedded double quotes escaped. Empty and NUL-containing names remain invalid.
- Append maps normalized CSV headers back to exact target column names before
  COPY. Mixed case, spaces, and hyphens no longer make every incoming column
  appear unexpected. Ambiguous normalized target names are rejected.
- Replace still deliberately uses DROP and CREATE. PostgreSQL requires the
  configured role to own the table; application code must not bypass that rule.
- Every queued, claimed, or running flow renders Stop. Cancelling a queued run
  does not terminate another flow's worker; assigned runs target their exact
  headed or headless worker process.

## Files Changed

- `app/flow_worker.py`: use explicit single left press-and-release, move the
  pointer away after every click, and ignore blue styling while hovered.
- `tests/test_flows.py`: reproduce a dropped final week click whose hover looks
  selected, require a retry, and assert exact click button/count/delay options.
- `app/flow_sql.py`: safely quote exact PostgreSQL catalog identifiers and map
  normalized append headers back to exact target column names before COPY.
- `tests/test_flow_sql.py`: cover spaced identifiers, embedded quotes, injection
  shaped text, the live 13-column append shape, exact COPY target names,
  ambiguous mappings, and the full mocked transaction paths.

## Commands And Checks

- Live target evidence before `c6da87f`: in a flow starting at week 27, weeks
  27 and 28 were selected but the final clicked week 29 was omitted.
- Root cause: the pointer remained over the final row, and the verifier could
  mistake ASAP's blue hover background for a successful selection.
- Live target evidence before `2fcad3a`: run 86 passed the scraper/download stage
  but failed before SQL connection because a discovered table name contained
  spaces and the application rejected it as an invalid bare identifier.
- Live target evidence before `c0a6b8a`: append connected but compared normalized
  CSV headers literally to mixed-case/spaced target columns, reporting all 13 as
  unexpected. Replace reached DROP but PostgreSQL SQLSTATE 42501 rejected it
  because the configured role does not own the table. Both transactions rolled
  back with no committed SQL changes. A later alternate target exposed no
  columns and was correctly reported as no longer existing.
- Full Python suite: `202 passed`.
- Targeted SQL suite: `23 passed`.
- Selection stress simulation: `20,000` randomized week cases passed with
  retained selections, zero through two dropped clicks, delayed rendered state,
  and hover false positives.
- `node --check app/static/app.js`: passed.
- `node tests/test_lineage_layers.mjs`: passed.
- `python3.11 -m py_compile app/flow_worker.py app/flow_sql.py`: passed.
- `git diff --check`: passed.
- Control-modifier source audit: passed with no scraper Control clicks.
- Citrix remained read-only. Nothing was updated, run, downloaded, edited,
  saved, refreshed, applied, published, exported, sent, or deleted remotely.

## Open Questions

- Commit `c0a6b8a` has not yet been installed or live-tested against PostgreSQL.
- Replace cannot succeed on the existing table until its PostgreSQL ownership is
  transferred to the configured role or an owner-controlled replacement process
  is provided.
- The repaired Stop behavior has not yet been live-tested with two queued flows.

## Next Step

With explicit approval, click Update App in Metronome and rerun append against
the existing target table. Confirm exact column mapping, COPY, and commit. Do not
use replace as an ownership workaround.
