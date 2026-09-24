# Part 1 — Flow SQL credentials from a `.env` file

Scope: backend, installer and wording; no journey change.

## Location and format

- The settings file is `$ProjectDir\.env`, beside `governance.db` and
  `endpoint_url.txt`, outside the code folder. In code:
  `BASE_DIR.parent / ".env"` (same precedent as `endpoint_url.txt` in
  `app/config.py`). Updates never touch it; the auditor reader, which can read
  the whole code folder, is explicitly denied.
- `DG_ENV_FILE` overrides the path; an empty value disables loading.
- UTF-8, tolerating the BOM Notepad writes. `NAME=value` or
  `export NAME=value`; `#` starts a comment line; everything after the first
  `=` is the value (no inline comments, so passwords may contain `#`); one pair
  of matching surrounding quotes is removed. Invalid lines are skipped and
  reported by line number only.
- A non-empty value overrides the process environment, so credentials can move
  out of Windows variables. Blank placeholders are ignored, so an untouched
  template changes nothing. Values are applied to `os.environ`, preserving the
  documented behavior that scripts inherit the worker environment.
- A missing or unreadable file never stops a process. `ENV_FILE_STATUS`
  records path, existence, loaded names, ignored line numbers and the error —
  never values.

## Changes

| Area | Change |
| --- | --- |
| `app/config.py` | `_load_env_file()` runs first, before any `os.environ` read; exposes `ENV_FILE` and `ENV_FILE_STATUS`. |
| `app/flow_worker.py` | Import `app.config` at the start of `main()` so `.env` applies before any child process; config is otherwise imported lazily inside `execute_flow`. |
| `app/flow_sql.py` | `configuration_status()` also returns the settings-file path. |
| `app/static/app.js` | Wording only: the SQL-connection note that points to a non-existent "Settings" page, and the missing-variable message, name the `.env` path and say to restart Metronome. |
| `app/flow_portable.py` | `CONFIG_SOURCE` gets the same small loader for generated `run_flow.py` files: `DG_ENV_FILE`, else the nearest `.env` in the generated file's parent folders (at most 8 levels). Standard library only. |
| `.env.example` | Committed template: `DG_UPLOAD_PGUSER=` and `DG_UPLOAD_PGPASSWORD=`, plus commented optional `DG_UPLOAD_PGHOST`, `DG_UPLOAD_PGPORT`, `DG_UPLOAD_PGDATABASE` (these fall back to `PG*`). No values. |
| `.gitignore` | Ignore `.env`; keep `.env.example` tracked. |
| `setup.ps1` | Create `$ProjectDir\.env` from `$CodeDir\.env.example` only when missing (never overwrite) and print its path with "fill in, then restart". Deny the auditor reader read access beside the `governance.db` denies; restrict the file to SYSTEM, Administrators and the installing user with separate `icacls` calls (`tests/test_auditor_managed.py` forbids `/inheritance:r /grant:r` in one call). |
| `tests/conftest.py` | New: set `DG_ENV_FILE=""` before any `app` import, so no test reads a real `.env`. |
| Docs | README "Settings file (.env)" section and environment rows; `docs/flow_standalone.md`; `docs/python_script_flows.md` inherited-environment note. |

## Tests

New `tests/test_env_file.py`:

- Parsing: BOM, `export`, quotes, `#` inside a value, comment and invalid
  lines (line numbers only).
- Precedence: a non-empty value overrides the environment; a blank value does
  not; `DG_ENV_FILE=""` disables loading; a missing file is fine; an unreadable
  file is recorded, not raised.
- Diagnostics and `configuration_status` never contain a value; the status
  names the file.
- The portable `CONFIG_SOURCE`, executed in a subprocess, reads `DG_ENV_FILE`
  and the nearest parent `.env`.
- `.env.example` holds exactly the two empty placeholders and no values;
  `.gitignore` ignores `.env` but not `.env.example`.
- `setup.ps1` string contract: create only when missing, auditor deny,
  separate `icacls` flags.

Re-run: `tests/test_auditor_managed.py`, `tests/test_pipelines.py`, the
portable-script tests and `tests/test_flow_builder_contract.mjs`.

## Operator steps after the merge

1. Update Metronome as usual; setup creates `.env` beside `governance.db`.
2. Fill in `DG_UPLOAD_PGUSER` and `DG_UPLOAD_PGPASSWORD`.
3. Restart Metronome (or run the update again) so every service reads it.
