# Agent Handoff

## Current Objective
Exclude folder-combine paths from scanned data sources.

## Repo State
- Path: data_governance
- Branch: main
- Latest commit: current folder-source filter work
- Public repo: yes
- Push status: push current changes after commit

## Decisions Made
- Email schedules are now keyed per BI profile, with selectable content types for open tasks, alerts, or both.
- Profile schedules support daily or week-days-only recurrence plus a send time.
- Lineage source details reuse the same freshness editor behavior as the Sources detail pane.
- Lineage Last Refreshed displays as `YYYY-MM-DD-HH-mm`, while the raw timestamp remains available as hover text.
- New indexes that depend on migrated columns must live in migrations, not the base schema block, because `init_db()` executes the base schema before migrations on existing databases.
- `Folder.Files(...)` queries are folder containers, not data-file sources, so scans skip them instead of adding them to Sources.
- Existing scan-discovered folder-like file sources are archived and unlinked from report tables on the next scan.

## Files Changed
- app/scanner/tmdl_parser.py: added folder-like file source detection using `Folder.Files(...)` and final-path extension checks.
- app/scanner/source_matcher.py: skips folder-like file sources before deduplicating scanned sources.
- app/scanner/runner.py: archives old scan-created folder sources and avoids linking folder paths into report lineage.
- docs/agent_handoff.md: updated current repo handoff.

## Commands And Checks
- `python3.12 -m compileall app/scanner/tmdl_parser.py app/scanner/source_matcher.py app/scanner/runner.py`: not run because `python3.12` is unavailable locally.
- `PYTHONPYCACHEPREFIX=/private/tmp/dg_pycache /opt/homebrew/bin/python3.11 -m compileall app/scanner/tmdl_parser.py app/scanner/source_matcher.py app/scanner/runner.py`: passed.
- Python behavior check: `Folder.Files(".../Inputs")` is filtered while `Excel.Workbook(File.Contents(".../sales.xlsx"))` remains a source.
- `git diff --check`: passed.

## Open Questions
- None blocking.

## Next Step
Run a normal scan and confirm folder paths no longer appear as active Sources.
