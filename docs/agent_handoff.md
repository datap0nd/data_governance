# Agent Handoff

## Current Objective

The targeted ASAP FOTA acceptance smoke is complete. Stop before any large
export and wait for the user to decide the next range and batch size.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest Citrix-deployed runtime commit: `ceb0448`
- Public repo: no, private
- Push status: maintained on `origin/main`; verify the current HEAD after each update
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
- ASAP's visible `Category = Weekly` control selects the report frequency. The
  exported column also named `Category` is a different business dimension. In
  the accepted output it contains `Domestic`, `Non-domestic`, and `Unknown
  (Incl. Wi...)`, not the filter value `Weekly`.
- The flow is manual and inactive. Do not start a current-year or other large run
  until the user finishes analysis and explicitly approves it.

## Files Changed

- `app/flow_worker.py`: discovers the visual-only Weekly/Daily Category filter,
  selects and verifies Weekly, preserves all populated multi-week matrix values,
  and normalizes the expanded `Weekly, YYYYWW...` download header safely.
- `transforms/asap_fota_unpivot_v1.py`: multi-week unpivot, sparse-cell handling,
  per-week diagnostics, week dates, reverse geolocation, and strict pass-through
  validation of the downloaded business `Category` dimension.
- `requirements.txt`: includes `pycountry` and `reverse_geocoder==1.5.1`.
- `tests/test_flow_worker_discovery.py` and transform tests: multi-week matrix,
  no-silent-truncation, and sparse-week coverage.
- `docs/metric_contracts.md`: 21-column FOTA snapshot contract and validation rules.

## Commands And Checks

- `PYTHONPATH=. uv run --python 3.13 --with pytest --with-requirements
  requirements.txt pytest -q`: 264 passed.
- Remote `main`: current local HEAD was verified equal to `origin/main` after
  publishing the Category pass-through change.
- Visible Citrix deployment: build `20260816-170611` from `ceb0448`; `setup.ps1`
  installed dependencies and registered the worker successfully.
- Live headed run `#139`: both two-week exports visibly had `Weekly` selected
  and `Daily` unselected. W30/W31 covered 2026-07-19 through 2026-08-01 and
  W32/W33 covered 2026-08-02 through 2026-08-15. Both XLSX downloads, both
  external transforms, and the isolated managed SQL replacement succeeded.
- Run `#139` file evidence: normalized source rows were 409,379 for W30/W31 and
  381,842 for W32/W33. Transformed rows were 460,621 and 429,482. PostgreSQL
  committed their exact sum, 890,103 rows, to
  `meto_db.bi_reporting.ASAP_Fota_Smoke`.
- Direct pgAdmin validation: total 890,103 rows and exactly four week suffixes.
  Per-week rows were W30=241,746; W31=218,875; W32=252,035; W33=177,447. The
  first pair sums to 460,621 and the second to 429,482, exactly matching the two
  transformed artifacts.
- Direct geolocation sample validation returned populated country/city/district
  values including Azerbaijan/Zyrya/Baki, Russian Federation/Zyablikovo/Moscow,
  Netherlands/Zwolle/Overijssel, and Germany/Zwickau/Saxony.
- Direct schema inspection returned 21 columns. The contracted projection is:
  `sell_out_region`, `sell_out_subsidiary`, `sell_out_country`, `country_code`,
  `operator`, `province`, `latitude`, `longitude`, `country`, `city`, `district`,
  `state`, `category`, `biz_sub`, `series`, `mkt_name`, `item`, `week`,
  `week_start_date`, `week_end_date`, `fota_value`.

## Open Questions

- Decide the intended handling of explicit zero FOTA values versus truly blank
  week cells. The accepted transform drops blanks and retains explicit zeros.
- Decide whether the isolated smoke table should remain for repeatable testing or
  be removed later. Do not delete it without explicit approval.

## Next Step

Stop and report the targeted smoke evidence. The Category fallback has been
removed in the repository; deploy that commit before any later production-table
run. Do not start another export without explicit user authorization.
