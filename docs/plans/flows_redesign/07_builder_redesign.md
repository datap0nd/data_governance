# Plan 7 — Collapsible builder preserving payload contracts

## Goal
Numbered Source, What to download, Where it goes, After download, Schedule and
owner steps with existing persistent summary.

## Current state
Both builders are coupled to _flowCollectBuilder, scanning, replication, export
syncing and SQL controls. Preserve IDs and behavior. No owner is valid server-side.

## Design
- One open step for create; all collapsed for edit. Omit inapplicable steps and
  compress numbers. Native header buttons with matching aria controls.
- Toggle existing DOM without losing fields/listeners. Next never submits.
  Status summaries read actual values; do not invent stricter client validators.
- ASAP/GSCM and existing Web sources retain discovery/scan/replication. Preserve
  exact Local worksheet and Outlook attachment rules.
- Managed destination read-only. New folder preview is labeled until ID assigned.
  Legacy edits retain target input and explicit adoption action.
- Transformation upload does not save. Preserve complete payload including
  nullable ASAP options, no-period mode and exact SQL target identity.
- Capture invalid events: open the containing step before focusing required
  fields. Structured server errors reveal their step; keep global fallback.
- Filename previews are illustrative. Rail stacks below 900px; long paths wrap.

## Step-by-step
Wrap existing groups → move into steps without ID changes → toggle/next/status/
validation reveal → source cards/destination → payload/interaction tests and CI.

## Risks
Hidden required fields can block submit invisibly. Do not disable Save based
on heuristic incomplete status. Preserve fields across network/scan errors.

## Acceptance criteria
Each source creates/edits correctly; toggles/errors preserve values; managed
flows need no target input; legacy edits remain valid; collectors, scans,
replication, upload and narrow layout verified.

