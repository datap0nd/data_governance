# Agent Handoff

## Current Objective

Make recurrence emails reproduce Power BI matrix number formatting and
expression-based visual titles without overriding semantic-model formatting
when the visual is still set to its default.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Feature commit: `c114319 Match recurrence formatting and dynamic titles`
- Public repo: no, private
- Push status: feature commit is pushed; follow-up formatting fix is uncommitted

## Decisions Made

- Do not trust `VisualDescriptor.title` by itself. Power BI can return a generic
  visual type such as `matrix`, `pivotTable`, `table`, or `tableEx`.
- Render the report before resolving visual titles.
- Read the supported authoring title property using the `titleText` selector,
  with `text` as a compatibility fallback for newer visual definitions.
- Accept generic words such as `Matrix` when they came from the explicit title
  property, but reject them when they came only from the descriptor.
- Refresh the builder title after visual discovery and preview.
- Refresh and persist an existing recurrence's title on its next draft or send
  run. If no usable live title is available, use `Alert results` in the email.
- Re-read the visual descriptor after report rendering so a resolved title is
  preferred over the pre-render generic visual type.
- When the title property contains a supported semantic-model measure
  expression, evaluate that measure through Execute Queries using the same
  report, page, visual, and slicer filters as the recurrence.
- Apply a scalar visual decimal-place setting when Power BI marks it explicit
  or omits the optional schema. Reject values explicitly marked with the
  default schema so `Auto` keeps the field or measure format string.
- Collect matrix data roles independently. One unsupported or empty role must
  not discard all field-format metadata. Fall back to the standard matrix
  `Rows`, `Columns`, and `Values` roles when capabilities are unavailable.
- Formatting changes only the recurrence's displayed CSV values or DAX output.
  It does not alter the semantic model or source values.

## Files Changed

- `app/pbi_visual_export.py`: resolve titles from rendered visual properties for
  discovery and export, recognize title measure expressions, evaluate dynamic
  titles, tolerate schema-less numeric precision, and collect matrix roles
  independently with standard-role fallback.
- `app/pbi_visual_query.py`: apply explicit zero-decimal visual formatting to
  value fields while retaining field formats when the visual setting is
  default.
- `app/routers/recurrences.py`: reject generic descriptor titles, update saved
  recurrence titles from live export metadata, and fall back safely.
- `app/static/app.js`: refresh the selected title after discovery and preview.
- `tests/test_pbi_visual_query.py`: verify formatting precedence, visual-export
  normalization, rendered descriptors, and dynamic title measure evaluation.
- `tests/test_recurrences.py`: verify saved generic titles are replaced, missing
  titles are omitted, and an explicit title named `Matrix` remains valid.
- `README.md`: document title evaluation and formatting precedence.

## Commands And Checks

- Full Python `pytest -q` suite after the follow-up collector change: 57 passed.
- Targeted recurrence and visual-query suite after the follow-up collector
  change: 52 passed.
- Python `compileall` for `app`: passed.
- `node --check app/static/app.js`: passed.
- Generated Power BI browser-runtime JavaScript syntax check: passed.
- `git diff --check`: passed.

## Open Questions

- The first live draft after `c114319` still showed decimals. The follow-up
  collector fix must be deployed and validated against the affected matrix.
- Unsupported title expressions or unavailable Build permission leave the
  existing safe `Alert results` fallback in place.

## Next Step

Create a draft from the affected matrix recurrence and confirm that the sales
average is displayed as a whole number and the email heading matches the
visible dynamic Power BI title.
