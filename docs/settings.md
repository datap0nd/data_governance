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
| `app/flow_job_limits.py` | The limits copied into each Flow job, and how a worker reads them |
| `app/scanner/limits.py` | How scanner code reads its settings without failing a scan |
| `app/routers/system_settings.py` | The page's API; every change is host PC only |
| `app/static/settings.js`, `settings.css` | The page |
| `app/static/app.js` (`settingsSubpages`) | The pages shown inside Settings that keep their own form |

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
  a run must travel in the job (`job["limits"]`). A run already queued or in
  progress keeps the values it was queued with, a retry or resume keeps the
  values of the run it continues, and a job without them behaves as before
  the setting existed.
- `restart`: avoid; nothing uses it today.

A schedule is rescheduled at once through `settings_registry.subscribe`. A
listener that fails is logged and never fails the save.

## Connections and secrets

Connection values (`store="file"`) are written to the protected settings file
(`.env`, one level above the code folder) because worker processes read them
too. `config.refresh()` applies a save to the web service at once; a worker
calls `config.refresh_if_changed()` on each loop. A password or token
(`kind="secret"`) is write-only: it can be replaced or cleared, is never sent
to a browser and is logged without its value. The ASAP and GSCM login is two such values
(`conn.portal_user`, `conn.portal_password`); Flow scripts read it from an
encrypted file, which `flow_credentials.sync_credential_file` keeps as a copy
of them. The AI key is kept encrypted for the Windows account the services
run as.

## Sections that keep their own form

AI, Paths, Flow workers and Premium Viewers were System menu entries. They are
now the **More settings** group at the end of the Settings rail. They are not
in the registry: each keeps its own renderer, binder, API and Save button in
`app/static/app.js`, and is listed in `settingsSubpages` there with its id,
its name in the rail, the words the search box matches (`about`) and whether
the server refuses a save from another computer (`hostOnly`). `settings.js`
draws the page in its panel area with the page's own functions and turns the
page's `h1` into a section title, so the journey on each page is unchanged.

- Addresses: `#settings/<section>` opens any section, registry or not, and
  the address follows the open section. The old addresses `#ai`, `#paths`,
  `#flow-settings` and `#premiumviewers`, and `navigate('<old id>')` from
  code, open the matching section.
- The shared **Save changes** bar, **Reset to default**, the source labels and
  the Event Log sentence in the page header describe registry settings only.
  The search box lists a matching section of this group as a link.
- A section that cannot load says so in its own place; the rest of Settings
  stays usable.
- Flows > Settings shows the Flow workers form as well and saves in place.
- The Flow workers form holds five values that are also registry settings
  (the Workers group under Flows: browser, total, background and visible
  capacity, recording wait). Both write the same stored values. After a
  section of this group was open, Settings reads the registry again, so the
  registry rows show what that form saved.

A page whose main content is configuration belongs here; a page that mainly
shows status or history, or runs work, stays in the System menu. That is why
Scanner and Updates did not move. To move a setting from one of these
sections into the registry, declare it as described above and remove its
control from the page's form.

## Who can change what

Everyone can read the page. Only the host PC can change a registry setting; see
"Access" in the [README](../README.md). The route helper is
`app.local_access.require_host`.

The sections that keep their own form keep their own rules, which the move
did not change: saving AI needs the host PC opened by a loopback address
(`localhost` or `127.0.0.1`), saving Paths, Flow workers and portal limits
needs the host PC, and anyone may start workers, check the impact of a path
change or save Premium Viewers. Their
controls are not disabled for another computer; the server refuses the save
and the page shows its sentence. `hostOnly` only decides whether Settings
shows another computer a notice above the section.

## What does not belong on the page

The Dubai time zone, the read-only rule for the probe account, the rule that
an owner's missing SQL password stops the Flow, the update repository and
branch, the 32-slot worker ceiling, and protocol or safety limits such as
payload sizes, lease and heartbeat pairs, page selectors and click-safety word
lists. Values that belong to one Flow, one source or one person stay on that
Flow, source or person.
