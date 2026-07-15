# Agent Handoff

## Current Objective

Add Tools > Recurrences to Metronome so a user can select a live Power BI table visual, filter its rows, split them into recipient subgroups, and send scheduled HTML emails through the existing Outlook implementation.

The first work-PC validation exposed a bootstrap failure before report loading: `page.wait_for_function` timed out because the original `cdn.powerbi.com` client URL no longer resolved. The client bootstrap now uses the published `powerbi-client` npm artifact from jsDelivr with an unpkg fallback and reports a direct proxy allow-list error instead of waiting for the full visual timeout.

## Repo State

- Working clone: `/private/tmp/data_governance_metronome`
- Branch: `main`
- Base commit: `4f5b405 Update handoff after UI rollback`
- Repository visibility: private.
- Delivery intent: commit the recurrence implementation and push it directly to `origin/main`, as explicitly requested by the owner.
- The pre-redesign interface remains the baseline. This feature adds only the Recurrences workflow and one small-system-menu responsive alignment fix.

## Implemented

- Added SQLite persistence for recurrence definitions, subgroup recipients, row rules, and run history.
- Added a one-minute scheduler dispatcher with daily, weekdays, selected-weekday weekly, and monthly schedules in the host's local timezone.
- Reused the existing saved Microsoft account token cache for Power BI report discovery and visual embedding. The token remains server-side.
- Added live workspace report and report-page discovery through the Power BI REST API.
- Added headless Microsoft Edge visual discovery and summarized CSV export through the official Power BI JavaScript client. The implementation selects exact technical page and visual identifiers and does not scrape the DOM.
- Added fail-closed behavior when the visual, subgroup column, or any rule column disappears.
- Added dynamic HTML table generation from every current exported column, so non-rule columns added by a report owner appear automatically.
- Added subgroup-specific recipient lists, exact case-insensitive subgroup matching, AND-combined row rules, and one Outlook email per subgroup with matching rows.
- Added `Create drafts`, confirmed `Run now`, run history, pause/enable state, and deletion confirmation.
- Added the Tools > Recurrences list and four-step creation/edit workflow.
- Added runtime diagnostics for cached authentication, Playwright, Edge, local timezone, timeout, and row limit.
- Replaced the non-resolving Power BI client URL with the package CDN URL used by Microsoft documentation and added a second CDN fallback.
- Added product and design contracts in `PRODUCT.md` and `DESIGN.md` for future interface work.
- Documented setup, runtime behavior, failure behavior, and environment variables in `README.md`.

## Important Decisions

- Recurrences use the saved delegated Power BI account, not the Data Governance scan output and not a copied bearer token. `get_access_token()` silently refreshes the existing encrypted token cache.
- The Power BI REST API discovers reports and pages. The official Power BI JavaScript client is required for visual discovery and `visual.exportData`, because the REST API does not expose arbitrary report visual data.
- Only table and matrix visuals are selectable.
- Export uses summarized data and is capped at 30,000 rows. `DG_PBI_VISUAL_EXPORT_MAX_ROWS` can lower but not raise the cap.
- The existing Outlook PowerShell launcher remains the delivery mechanism. A successful run means Outlook accepted the launch request, consistent with current Metronome email semantics.
- Scheduled failures advance to the next recurrence time instead of retrying every minute. Manual runs do not alter the saved next-run time.
- Runs with zero matching rows send nothing and record `no_rows`.
- Existing detector warnings for legacy side-tab accents, bounce easing, and layout-property transitions were left unchanged because they predate this feature and the owner explicitly requested preserving the established interface.

## Files Changed

- `app/config.py`
- `app/database.py`
- `app/main.py`
- `app/scanner/pbi_fetch.py`
- `app/pbi_visual_export.py`
- `app/routers/recurrences.py`
- `app/static/index.html`
- `app/static/app.js`
- `app/static/style.css`
- `requirements.txt`
- `tests/test_recurrences.py`
- `README.md`
- `PRODUCT.md`
- `DESIGN.md`
- `docs/agent_handoff.md`

## Verification

- `env PYTHONPATH=. /tmp/data-governance-test-venv312/bin/python -m pytest -q`: passed, 17 tests.
- `node --test tests/test_lineage_layers.mjs`: passed.
- `node --check app/static/app.js`: passed.
- Python compilation of all changed backend modules: passed.
- `git diff --check`: passed.
- Impeccable layout detector: no findings.
- Impeccable typography detector: no findings.
- Local browser pass: list view, runtime warning, missing-auth failure state, populated recurrence row, history expansion, and primary action visibility passed with no console errors.
- Secret-pattern scan across the changed application, tests, and documentation: no findings.

## Remaining Live Validation

This environment does not have the work PC's encrypted Power BI token cache, real workspace access, Windows Edge runtime, or Outlook profile. On the work PC, validate one real recurrence in this order:

1. Open Tools > Recurrences and confirm the cached account, Edge, and Playwright status indicators are ready.
2. Select a real report, page, and table visual and fetch the preview.
3. Save the recurrence paused or with a future schedule.
4. Use Create drafts and inspect each subgroup draft, including HTML columns, rule filtering, and recipients.
5. Use Run now only after the drafts are correct, then confirm the run history.
6. Confirm whether the chosen report's filters and bookmarks produce the expected summarized export. This is the only known behavior that still requires a real-report test.

## Next Step

Deploy or update Metronome on the Windows work PC, then perform the live validation above before relying on the first unattended send.
