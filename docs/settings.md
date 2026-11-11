# Settings

**System > Settings** is one page for every app-wide connection, folder,
schedule and limit. This document is for whoever adds or changes a setting.

## The rule

A setting is declared once, in the registry, together with the code that
reads it. The page, the validation, the allowed range, the Event Log entry and
the "where does this value come from" label are all drawn from that one
declaration. A value that is only a constant in code is not a setting; a
setting nobody reads is refused by
`tests/test_settings_registry.py::test_no_setting_is_declared_without_code_that_reads_it`.

| File | What lives there |
| --- | --- |
| `app/settings_registry.py` | `Setting`, `register`, `get`, `snapshot`, `save`, `subscribe`, secrets |
| `app/settings_catalog.py` | Connections, folders and the settings that existed before the page |
| `app/settings_catalog_scanner.py` | Scanner, freshness, update and backup settings |
| `app/settings_catalog_flows.py` | Flow run, worker, SQL step and delivery settings |
| `app/routers/system_settings.py` | The page's API; every change is host PC only |
| `app/static/settings.js`, `settings.css` | The page |

## Where a value comes from

In this order: the value saved on the page, then the environment variable
named in the declaration's `env=`, then the default. The default is the
behaviour the code had before the setting existed, so declaring a setting
changes nothing until someone edits it. **Reset to default** removes the
saved value and hands control back to the environment variable, if any.

## When a change applies

Every row says which of these holds, from the declaration's `applies`:

- `now`: the web service reads the value each time it is used.
- `next_scan`: read when a scan starts.
- `new_runs`: copied into each Flow job when the job is queued. Worker
  processes never read Metronome's database, so anything a worker uses during
  a run must travel in the job. A run already queued or in progress keeps the
  values it was queued with, and a job without them behaves as before the
  setting existed.
- `restart`: avoid; nothing uses it today.

A schedule is rescheduled at once through `settings_registry.subscribe`. A
listener that fails is logged and never fails the save.

## Connections and secrets

Connection values (`store="file"`) are written to the protected settings file
(`.env`, one level above the code folder) because worker processes read them
too. `config.refresh()` applies a save to the web service at once; a worker
calls `config.refresh_if_changed()` on each loop. A password or token
(`kind="secret"`) is write-only: it can be replaced or cleared, is never sent
to a browser and is logged without its value. The ASAP and GSCM login and the
AI key are kept encrypted for the Windows account the services run as.

## Who can change what

Everyone can read the page. Only the host PC can change anything on it; see
"Access" in the [README](../README.md). The route helper is
`app.local_access.require_host`.

## What does not belong on the page

The Dubai time zone, the read-only rule for the probe account, the rule that
an owner's missing SQL password stops the Flow, the update repository and
branch, the 32-slot worker ceiling, and protocol or safety limits such as
payload sizes, lease and heartbeat pairs, page selectors and click-safety word
lists. Values that belong to one Flow, one source or one person stay on that
Flow, source or person.
