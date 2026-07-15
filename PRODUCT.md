# Metronome Product Context

## Product

Metronome is an internal web application for BI and data teams. It combines
data-governance monitoring, ownership, lineage, refresh status, operational
tasks, and scheduled communication in one local service.

## Primary users

- BI analysts and report owners who understand reports, tables, columns, and
  business rules but should not need to write code for routine monitoring.
- Data team leads who need a reliable view of scheduled processes, alerts, and
  delivery outcomes.

## Core problem

Operational BI work is split across Power BI, source systems, task tracking,
and Outlook. Metronome turns those separate signals into explicit checks,
owners, schedules, and actions.

## Product goals

- Make data and report failures visible before users discover them.
- Turn repetitive operational checks into understandable scheduled workflows.
- Preserve traceability through run status, timestamps, row counts, and errors.
- Fail safely when data, permissions, or report structure changes.
- Keep advanced automation configurable without exposing authentication tokens
  or requiring users to maintain scripts.

## Brand personality

Metronome should feel quiet, precise, operational, and trustworthy. It is a
dense working tool, not a marketing surface. Copy should be direct and explain
what happened and what the user must do next.

## Interface direction

The existing product interface is the visual reference. Preserve its bundled
Outfit typography, warm neutral surfaces, restrained teal accent, compact data
tables, familiar navigation groups, and low-decoration product layout.

Avoid decorative dashboards, gradients, nested card grids, floating panels,
and unrelated redesigns. New workflows should use full-page structures when
they contain multiple dependent steps.

## Accessibility and resilience

- All controls must be keyboard accessible and visibly focused.
- Do not communicate status through color alone.
- Long names and errors must wrap without breaking the layout.
- Destructive actions must name the item and consequence.
- Scheduled communication must fail closed when its saved Power BI page,
  visual, subgroup column, or rule column no longer exists.
