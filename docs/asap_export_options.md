# ASAP export options

ASAP Flows store the Export Wizard choice separately from the physical file
format. The five supported semantic choices are the labels shown by ASAP:

- `Excel with plain text`
- `CSV file format`
- `Excel with formatting`
- `HTML`
- `Plain text`

The two Excel choices can produce `.xls` or `.xlsx`; Metronome detects and
preserves the real workbook/container instead of trusting the configured
suffix. HTML and Plain text are download-only. They cannot be transformed or
sent to SQL.

## Report title and filter details

`Export Report Title` and `Export filter details` are saved independently.
New ASAP Flows default both controls to checked. Existing Flows migrated from
the old CSV/Excel setting keep both values null, which means the worker leaves
the portal control untouched. A successful detailed scan may replace a null
with the observed state only when the selected type exposes that control and
every selected export view agrees.

Detailed discovery opens each export view's Export Wizard without exporting.
It records the five available types plus checkbox availability and state,
restores the original controls, and closes the wizard. A wizard inspection
failure is a warning for that view; it does not remove the report or its
filters. The builder intersects capabilities across all selected views.

At run time the worker selects and verifies the semantic type, then enforces
every non-null checkbox setting. A missing type or requested control is an
actionable failure listing what was detected. The compact raw-table direct
Excel shortcut is used only for inherited checkbox state and only for
`Excel with plain text`; explicit checkbox choices always require the full
wizard.

## Saved artifacts

- CSV keeps the normalized UTF-8 CSV at the configured filename, preserving
  the established primary-artifact contract. The exact portal response is
  saved beside it as a collision-safe `_raw.csv` original artifact. Report
  title and filter-detail preambles are excluded from the normalized table by
  blank-section and rectangular-table validation.
- Excel preserves the original workbook or validated HTML/XML-based `.xls`
  response and writes a `_normalized.csv` sibling for transformations and SQL.
- HTML preserves the exact `.html` response only when HTML was explicitly
  selected. Sign-in/error markers are rejected and a usable multi-row report
  table is required.
- Plain text preserves the exact `.txt` response only when Plain text was
  explicitly selected. It must decode cleanly, contain no markup, binary or
  sign-in/error markers, and form a plausible multi-row table.

Replay recipes include the semantic type and both checkbox values, so exports
with different wizard settings never share a request recipe.

## Release verification

A headed targeted scan and representative export of all five types is required
before release. Confirm the real type labels, checkbox association per type,
CSV controls, emitted suffix/container, and whether ASAP's direct Excel action
is byte/structure-equivalent to `Excel with plain text`. Update the registry or
resolver for any portal-specific mismatch before considering this feature
verified.
