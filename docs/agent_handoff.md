# Agent Handoff

## Current Objective

Deliver Power BI field-format fidelity and the missing business title in
recurrence previews and recipient emails.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest base commit: `7f39f90 Update alert message handoff`
- Public repo: no, private
- Push status: implementation is complete locally and pending publication

## Decisions Made

- Keep Power BI's native summarized CSV as the authority for rich currency,
  percentage, locale, date, and dynamic formatting.
- Read the selected visual's live field format strings on the primary export
  path as well as the existing Execute Queries fallback.
- Locally normalize only fields explicitly formatted as whole numbers, including
  custom `0`/`#,0` formats and standard `F0`/`N0` formats.
- Apply normalization before recurrence previewing, rule evaluation, subgroup
  extraction, and email rendering.
- Use the selected Power BI visual title as the results heading. The report name
  remains the report-summary title, and untitled visuals fall back to `Alert
  results`.

## Files Changed

- `app/pbi_visual_export.py`: collect field metadata on successful summarized
  exports and apply safe whole-number normalization.
- `app/pbi_visual_query.py`: detect whole-number Power BI formats, normalize
  numeric CSV cells, and recognize the authoring API's `isHidden` field marker.
- `app/routers/recurrences.py`: render the escaped visual title above the email
  results table.
- `tests/test_pbi_visual_query.py`: cover custom and standard integer formats,
  scaling-format exclusion, runtime metadata, and primary-export integration.
- `tests/test_recurrences.py`: cover escaped visual titles and delivered-email
  headings.
- `README.md`: document title and field-format behavior.

## Commands And Checks

- Full Python 3.11 `pytest -q` suite: 48 passed.
- Python `compileall` for `app`: passed.
- `node --check app/static/app.js`: passed.
- `git diff --check`: passed.

## Open Questions

- Live validation against the work PC's Power BI report and Outlook profile has
  not been run from this Mac. The safe formatter intentionally leaves scaled,
  percentage, currency, date, locale-specific, and dynamic formats to Power BI.

## Next Step

Create Outlook drafts from a recurrence containing a whole-number field and
confirm the preview and email show the Power BI visual title and no redundant
decimal places.
