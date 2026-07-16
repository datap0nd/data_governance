# Agent Handoff

## Current Objective

Keep Tools > Recurrences tied to an exact Power BI table visual while fixing
`Error running visual data query` without automating the Power BI export menu.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Base commit: `750b8bb Harden Power BI Edge lifecycle`
- Public repo: no, private
- Push status: implementation is ready for its direct `origin/main` commit and push

## Decisions Made

- Keep the existing official embedded-client path for exact report, page, and
  visual discovery. Do not click Power BI UI controls or scrape the report DOM.
- Continue trying `visual.exportData` first because it preserves native Power BI
  summarized export when the API supports the visual.
- When Power BI returns a visual-data-query error, use Microsoft's report
  authoring APIs to read the selected visual's current fields, format strings,
  report/page/visual filters, and slicer filters.
- Convert only structured Power BI targets into escaped DAX and call the official
  Execute Queries REST endpoint with the existing cached delegated account.
  No user-supplied DAX, copied token, or second authentication flow is introduced.
- Fail closed instead of sending approximated or partial data. The REST fallback
  currently supports normal columns, explicit column aggregations, measures,
  basic filters, advanced filters, and standard slicer selections. It rejects
  visual calculations, hierarchies, percent-of-grand-total fields, Top N,
  relative date/time, identity, and multi-field filters.
- Execute Queries fallback access depends on semantic-model Read and Build
  permission and the tenant setting `Dataset Execute Queries REST API`.
- The existing side accent borders flagged by the design hook are intentional
  severity and diagnostics markers that predate this backend-focused change.
  They were reviewed and left unchanged.

## Files Changed

- `app/pbi_visual_export.py`: load Microsoft's authoring extension, capture
  structured Power BI errors, and invoke the query fallback whether the error
  arrives from render, the report error event, or `exportData`.
- `app/pbi_visual_query.py`: build escaped DAX from visual metadata, execute it
  with the cached account, apply static visual formats, convert results to CSV,
  and enforce fail-closed limits and permissions errors.
- `app/routers/recurrences.py`: pass workspace and dataset IDs to previews and
  scheduled runs, and expose the preview export method.
- `app/static/app.js`: include workspace and dataset IDs in preview requests.
- `tests/test_pbi_visual_query.py`: cover DAX generation, escaping, formatting,
  filters, unsupported constructs, row-limit handling, cached authentication,
  CDN fallback, and exporter integration.
- `README.md`: document the no-UI-automation architecture, permissions, dynamic
  field behavior, and fail-closed limitations.

## Commands And Checks

- `uv run --python /opt/homebrew/bin/python3.11 --with-requirements requirements.txt --with pytest python -m pytest -q`: 34 passed.
- `uv run --python 3.14 python -m py_compile ...`: passed for all changed Python modules and tests.
- `node --check app/static/app.js`: passed.
- Extracted JavaScript from `_RUNTIME_HTML` and ran `node --check`: passed.
- `git diff --check`: passed.
- Secret-pattern review of changed files: no real credentials or internal identifiers found.
- Live Windows Power BI validation was not run because this Mac does not have
  the work PC token cache, workspace access, Edge runtime, or Outlook profile.

## Open Questions

- The work PC account may already have Build permission and the tenant Execute
  Queries setting, but only a live preview can confirm that tenant-side access.
- Report authoring field introspection is official but still needs one live test
  against the affected table and its real filter configuration.

## Next Step

Update Metronome on the work PC, restart it, open the affected recurrence, and
fetch the preview. If tenant access is available, the preview should report
`execute_queries` and return the table instead of the visual-query error. If it
is not available, Metronome will show the exact Build/tenant-setting requirement
and send no email.
