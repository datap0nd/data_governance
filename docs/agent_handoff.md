# Agent Handoff

## Current Objective

Pause before any large ASAP FOTA export and analyze the verified four-week
multi-week smoke. No further export is authorized until the user explicitly
approves it.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest runtime commit before this handoff update: `e733850`
- Public repo: no, private
- Push status: `e733850` verified on `origin/main`; publish this handoff update next
- Preserve untracked `governance.db-shm` and `governance.db-wal`

## Decisions Made

- FOTA unpivot and reverse geolocation remain in the external MX Share Python
  transform. Metronome only orchestrates downloads, generic transform execution,
  and the atomic SQL handoff.
- A multi-week ASAP workbook contains one value column per displayed week. The
  transform emits one row per populated week cell and derives Week Start Date and
  Week End Date for every emitted row.
- Geolocation follows the authorized legacy script: Country from `cc`, City from
  `name`, District from `admin1`, and State from `admin2`.
- Managed snapshot SQL normalizes CSV headers to lowercase snake_case identifiers.
  The 21-column SQL contract therefore ends with `week`, `week_start_date`,
  `week_end_date`, and `fota_value`.
- Small acceptance uses two XLSX exports with two weeks each: 2026-W30/W31 and
  2026-W32/W33. SQL writes to isolated
  `meto_db.bi_reporting.ASAP_Fota_Smoke`, never the production `ASAP_Fota` table.
- The flow is manual and inactive. Do not start a current-year or other large run
  until the user finishes analysis and explicitly approves it.

## Files Changed

- `app/flow_worker.py`: preserves all populated multi-week matrix values and
  recovers week columns only when file width proves a safe one-to-one mapping.
- `transforms/asap_fota_unpivot_v1.py`: multi-week unpivot, sparse-cell handling,
  per-week diagnostics, week dates, and reverse geolocation.
- `requirements.txt`: includes `pycountry` and `reverse_geocoder==1.5.1`.
- `tests/test_flow_worker_discovery.py` and transform tests: multi-week matrix,
  no-silent-truncation, and sparse-week coverage.
- `docs/metric_contracts.md`: 21-column FOTA snapshot contract and validation rules.

## Commands And Checks

- `PYTHONPATH=. uv run --python 3.13 --with pytest --with-requirements
  requirements.txt pytest -q`: 259 passed in 2.80s.
- Remote `main`: `e73385043ea7865e571ea73a4441ea341d7ce858` verified before
  this handoff update.
- Visible Citrix deployment: build `20260816-141512` from `e733850`; `setup.ps1`
  installed dependencies and registered the worker successfully.
- Live run `#133`, SQL disabled: two two-week downloads and both external
  transforms succeeded. Source rows were 405,301 for W30/W31 and 357,878 for
  W32/W33. Transform rows were 810,602 and 715,756, with per-week counts
  W30=405,301, W31=405,301, W32=357,878, W33=357,878.
- Live run `#134`, SQL enabled to the isolated smoke table: succeeded from
  14:47:03 to 15:01:06 Dubai time. Transformation took 4m07s, SQL insertion
  took 1m28s, and total runtime was 14m03s. PostgreSQL atomically created the
  target and committed 1,526,358 rows from two transformed files.
- Direct pgAdmin validation of `meto_db.bi_reporting.ASAP_Fota_Smoke`: 1,526,358
  rows, four distinct weeks, minimum 2026-W30, maximum 2026-W33. Per-week rows:
  W30=405,301; W31=405,301; W32=357,878; W33=357,878.
- Direct geolocation coverage: Country=1,460,100; City=1,460,100;
  District=1,454,210; State=150,178. Visible samples included Kaleybar in East
  Azerbaijan and Razan in Hamadan.
- Direct 21-column projection succeeded for:
  `sell_out_region`, `sell_out_subsidiary`, `sell_out_country`, `country_code`,
  `operator`, `province`, `latitude`, `longitude`, `country`, `city`, `district`,
  `state`, `category`, `biz_sub`, `series`, `mkt_name`, `item`, `week`,
  `week_start_date`, `week_end_date`, `fota_value`.

## Open Questions

- Each two-week workbook produced the same row count for both weeks. A prior
  single-week W33 smoke had 150,724 rows, while W33 in the two-week matrix has
  357,878 rows. Determine whether ASAP emits a dense union of item rows with
  zero/blank semantics across selected weeks before deciding that ten-week files
  are safe.
- Decide the intended handling of zero FOTA values versus truly blank week cells.
  The current transform drops blank cells and retains explicit zero values.
- Decide whether the isolated smoke table should remain for repeatable testing or
  be removed later. Do not delete it without explicit approval.

## Next Step

Discuss the row-shape discrepancy and batching tradeoff with the user. Do not run
another export until the user explicitly approves the next range and batch size.
