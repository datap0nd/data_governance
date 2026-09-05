# Plan 6 — Accessible per-group sorting

## Goal
Sort rows within fixed source groups; persist for session; reset to API order.

## Current state
Generic dataTable owns one flat tbody. Reuse styling and add a small pure sorter.

## Design
- Keys: name/source/owner/type/to/schedule/lastRun/active. Actions unsortable.
- Cycle first direction → opposite → API order. Last run starts descending,
  accurately reported by aria-sort. Null/invalid dates and absent owners stay
  last in either direction; never use a direction-reversing sentinel.
- Stable ties preserve API order; do not mutate input arrays.
- Native header buttons, aria-sort only on active header, validated persisted
  key/direction. Sorting retains expanded groups and focus through polls.

## Step-by-step
Comparator/state helpers → header/binding → focus/arrow CSS → tests/CI.

## Risks
Newest-first is not ascending. Native buttons already implement keyboard events;
extra keydown handling can accidentally sort twice.

## Acceptance criteria
Every column in both directions, nulls/ties/date validity, immutability, third
click exact reset, storage failure resilience and matching accessible direction.

