# Plan 5 — Grouped Flows list and folder access

## Goal
Collapsed source groups ASAP/GSCM/Outlook/Local/Web. Columns: Flow, Source, Owner,
Type, To, Schedule, Last run, Active, Actions.

## Current state
_flowListHtml at app/static/app.js:10819 is flat; tests slice up to the report
helper marker. Polling replaces list markup. _flow_out lacks category_path.

## Design
- One table, stable group IDs and native header buttons with aria-expanded.
  Counts expose running/failing flows even when collapsed; omit empty groups.
- Pure shared row model. Source type file maps to Local; unknown portals to Web.
  SQL display preserves actual identifier case. Local without SQL says Private
  snapshots, never original-name publication. Keep remote-control IDs.
- SessionStorage stores validated open groups with unavailable/corrupt fallback.
  Preserve state and keyboard focus on polls; escape all user values.
- Keep action classes and Run/Stop/Active behavior. Native details overflow is
  acceptable; don't assume a modal helper implements menu keyboard behavior.
- Folder endpoint resolves this flow's validated folder only. For remote clients
  and service session 0 return a copyable path. Launch Explorer only from a
  verified interactive local process.
- Do not introduce a mutable-path scheduled-task command channel. A future fixed
  interactive helper needs a separate authenticated protocol.
- Clipboard action supports insecure HTTP fallback and explains opening locally.

## Step-by-step
Folder endpoint → row/group renderer → persisted toggle/actions → compact CSS →
group/state/output-semantics tests in CI, preserving no-Freshness regression.

## Risks
Session 0 Explorer is invisible; never return opened=true for that launch.
Only explicit folder actions should probe potentially remote filesystem paths.

## Acceptance criteria
Correct groups/counts/columns, escaped long values, collapsed/persisted state,
existing actions, copy fallback and keyboard controls.

