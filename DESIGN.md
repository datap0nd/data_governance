# Metronome Design Contract

## Register

Product UI. Favor predictable structure, compact density, and familiar
controls over decorative variation.

## Typography

- Family: bundled Outfit with the existing system fallback.
- Page title: 1.85rem, weight 800.
- Section title: 1.05rem, weight 700.
- Component title: 0.95rem, weight 600 or 700.
- Body and control text: 0.82rem to 0.88rem, weight 400 or 500.
- Metadata and labels: 0.68rem to 0.76rem, weight 500 or 600.
- Keep essential instructions and validation messages at 0.78rem or larger.
- Use tabular numerals for schedules, row counts, and run history.

## Spacing

Use a 4px-based scale for new components:

| Token | Value | Use |
|---|---:|---|
| `--space-xs` | 0.25rem | Inline icon and label gaps |
| `--space-sm` | 0.5rem | Tight control groups |
| `--space-md` | 0.75rem | Component internals |
| `--space-lg` | 1rem | Panel padding |
| `--space-xl` | 1.5rem | Distinct section separation |
| `--space-2xl` | 2rem | Major workflow separation |

Use spacing and dividers before adding another card. Do not nest bordered cards
inside bordered cards.

## Color

Reuse the existing CSS variables. Teal is the primary action and focus color.
Green indicates successful execution, amber indicates attention, and red
indicates failure or destructive action. Every colored status also needs text.

## Components

- Page header: title and concise subtitle on the left, primary action on the
  right.
- Builder: full-page numbered steps with one visible working section and a
  persistent summary rail on wide screens.
- Tables: compact rows, sticky or clear headers where useful, and horizontal
  overflow rather than squeezed columns.
- Empty states: explain the value and provide a specific next action.
- Loading states: name the operation and set expectations for Power BI loads.
- Run history: show status, trigger, started time, row counts, email count, and
  actionable errors.

## Interaction

- Disable dependent fields until their parent selection is complete.
- Preserve entered configuration when a network or export operation fails.
- Prevent duplicate submissions while an operation is running.
- Preview the exact live visual before subgroup and rule configuration.
- Require explicit confirmation before sending a manual test email or deleting
  a recurrence.
- Scheduled execution must never guess a replacement visual when an identifier
  disappears.

## Responsive behavior

- At wide widths, use a main builder column and a narrower summary column.
- Below 900px, stack the summary under the builder.
- Tables scroll horizontally.
- Action groups wrap without obscuring the primary action.

## Usability review for changed journeys

Build a clickable local preview with fictional data using existing components,
fonts and design tokens. Obtain owner feedback before implementing a changed
journey. Approval applies to the demonstrated journey; material changes return
for review. Small wording or spacing fixes require usability review but no
separate approval pause.

Walk every changed control, including failure and recovery. Check clear labels,
visible feedback beside actions, preserved work, predictable navigation and a
clear next action. Record actual browser evidence tied to the tested revision.
Favor minimal screens and contextual questions over permanent configuration
forms; never hide a required choice under Advanced.
