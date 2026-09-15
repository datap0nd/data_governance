---
name: html-replicate
description: Reconstruct a report from a folder of prior Excel reports and source files, clarify its business definitions, map supporting data and produce standalone interactive HTML with CSV exports. Use for /html_replicate, including Korean business-trip report workbooks.
---

# Replicate a report from its evidence

The input is a reporting folder, not a specification the user has to write.
Understand it, ask the questions that matter, and build the report. Gemini owns
the analysis, generated HTML, CSVs and follow-up answers. Metronome only supplies
source/flow operations when needed; activate `metronome` for those operations.

## Understand the references

Call `prepare_workspace` before generating any files. Inventory the specified
source folder using the CLI's file tools. Preserve originals; create a named
report folder under the returned `reports_directory`, and put all temporary
scripts, intermediate files, environments and caches in `working_directory`.
Set explicit working directories and absolute output paths for every tool call;
never create generated work in the Metronome checkout or source folder. If no
folder was supplied, ask for it. Treat file paths as literal values when invoking
tools, including spaces, Korean characters and shell metacharacters.

Use an available spreadsheet tool or a standard library to inspect Excel. The
Metronome MCP's `capabilities` result locates the installed ExcelJS library for
Node.js when no spreadsheet connector exists. You may write task-specific code
in `working_directory` as needed; retain reusable report code with the finished
report in `reports_directory`. No fixed extraction script or custom workbook
tool is required. ExcelJS reads .xlsx; for legacy .xls, encrypted or protected
files, use an appropriate installed tool or request an accessible copy. Do not
pretend a CSV conversion preserved layout or formulas.

Identify tables within sheets, merged/multirow headers, Korean labels, formulas
and cached results, units, date meanings, subtotals, hidden rows/columns and
report-to-report differences. Do not assume a worksheet is one table. ExcelJS
does not recalculate formulas: label cached/missing results and validate needed
calculations independently before claiming accuracy.

Keep an evolving map from each original table, metric and attribute to its
source, calculation, joins, filters and evidence. Preserve Korean labels and
the original hierarchy unless the user requests a change. Distinguish facts
observed in files from inferred business meaning. Ask focused questions after
inspection: expected subsidiaries, travel versus booking dates, fiscal versus
calendar periods, currencies, VAT, duplicates and adjustment rules. Do not ask
the user to manually enumerate all fifteen downloads if you can discover them.

## Find and reconcile inputs

Start with supplied source files and existing Metronome catalogs/flows. Use a
connected browser tool for independent portal exploration and downloads when
needed; tell the user exactly which capability is missing if none is connected.
Do not silently install or invent a browser connection, reuse protected browser
state, or label a replay of the same flow as an independent check.

Use the dedicated SQL reader through the metronome skill. Compare subsidiaries,
period coverage, blanks/nulls, duplicates, range/distribution outliers, row counts
and totals at the correct grain. Distinguish download completeness, SQL loading
completeness and reproduction of the business calculations. Missing-source
coverage stays explicit; do not fill unknowns with zero or claim a sample proves
that all subsidiaries exist.

Propose reusable Metronome flows for repeatable acquisition/loading only after
the mappings are understood. Reuse existing definitions. Keep unresolved questions
and partial work so a resumed conversation continues from evidence rather than
starting over. Confirm SQL creation/load behavior through the metronome workflow.

## Deliver the standalone report

Build a self-contained HTML report in the reporting output folder using tools
you choose. Match the reference's business sections and dimensions; adapt the
layout for readable interactive use instead of flattening everything into a
single grid. Include the original attributes as appropriate filters, grouping,
column selection and sorting. CSV exports must represent the selected table,
filters and columns, preserve Korean text (UTF-8 with BOM for Excel), and handle
quotes/newlines and spreadsheet formula-injection values safely.

Keep the HTML usable offline: embed required data/styles/scripts; no credentials,
live database connection, CDN dependencies or automatic network requests. Render
untrusted cell text safely. Include source/period references, generated time,
validation findings and visible unresolved/missing inputs. Do not overwrite a
previous complete report with an unmarked partial result.

Verify the actual generated report: walk filters, grouping, column choices,
sorting, reset and CSV downloads; reconcile displayed and exported totals with
the source mapping. Use a browser tool when connected. Otherwise validate data
and generated markup and explicitly identify the missing visual verification.
Never claim a browser check happened when it did not.

Return the HTML path and important remaining questions. Retain the source map,
validation notes, relevant flow/run IDs and generation code beside the output
so future updates and answers use the same definitions. Follow-up questions use
the mapped data and state when a fresh download/query is needed.


Flow acquisition must follow the metronome skill's mandatory independent-download,
recorded-flow and final-verification sequence. Show DATA DIFFERENCES/INCOMPLETE
visibly in the report; never promote a successful execution to a verified match.
Keep the actual verification evidence IDs and declared table scopes beside the
source map. No Metronome HTML feature or recurring generator is added.
