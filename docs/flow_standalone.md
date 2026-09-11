# Running a saved Flow without Metronome

Every managed Flow automatically maintains these files after committed changes:

- `flow.json` in the Flow folder: name, owner/contact, creator, schedule and Dubai timezone, source and bookmark/report route, saved filters, output policy, transformation and SQL settings, browser/timing defaults, and active/latest recording definitions.
- `Scripts/run_flow.py`: one plain Python file with the frozen configuration (`FLOW`, readable JSON) and the execution code this Flow needs. Catalog/bookmark, local-file and Outlook Flows include their execution code as well as recorded Flows. No installed Metronome application or running server is required. Each `# ==== module: NAME ====` section is copied verbatim, comments included, from `app/NAME.py` in the Metronome repository; a small loader at the top of the file imports those sections as `_mf.NAME` using the file's real line numbers, so tracebacks, breakpoints and editors point at the lines you see.
- `Scripts/README.md` (this guide) and `Scripts/requirements.txt`: operator instructions and execution libraries.
- `Scripts/versions/`: prior generated executable revisions (`run_flow-<hash>.py`), archived copies of any `run_flow.py` you edited (`run_flow-<hash>-edited-<UTC time>.py`), and previous documentation/requirements copies. Existing operator notes and custom dependency lists are archived before those generated files refresh. Preserve the whole Flow folder, including transformation scripts; consult archived notes and dependency lists when handing over.

There is no Generate standalone step. Save/edit normally; owner, schedule, source/catalog, recording and shared preference changes also refresh the derived files, and so does updating the Metronome application, because the script always embeds the current execution code. Startup reconciles existing managed Flows. SQLite remains the source of truth: editing these files does not edit or schedule a Flow in Metronome. `configuration` in flow.json describes the saved Flow; `handover.state` indicates whether its script is `current`, `draft` (the Flow cannot run yet) or `error` (the files could not be updated). These are continuity copies, not a database restore format or a backup of run history/data.

## First-time setup on Windows

Use Python 3.11 or newer. In PowerShell, change to this Scripts directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_flow.py --dry-run
.\.venv\Scripts\python.exe run_flow.py --headed --no-sql
```

The generated Python header lists the library versions available when it was saved; use those versions when reproducing an environment. Install the saved Chrome or Edge browser. Outlook requires Windows, classic Outlook configured for the operator, and permission to run its per-run interactive Scheduled Task. The PowerShell attachment helper is embedded in the saved Python file; pywin32 is not required. Transformation scripts can require extra libraries or files: preserve their Scripts folder and install those declared by the script's maintainer.

Dry-run checks the saved configuration without downloading, changing output files or connecting to SQL. A normal run **does** publish to the saved destination; use a dedicated test Flow/destination for rehearsals. Run under an account with access to the shared folders, website, Outlook and SQL target. Initial interactive SSO may be required. Credentials/cookies are not exported: configure the caller's credential provider and SQL environment separately. SQL uses `DG_UPLOAD_PGHOST`, `DG_UPLOAD_PGPORT`, `DG_UPLOAD_PGDATABASE`, `DG_UPLOAD_PGUSER` and `DG_UPLOAD_PGPASSWORD` (never paste secrets into these saved files).

## Routine execution and scheduling

```powershell
.\.venv\Scripts\python.exe run_flow.py
.\.venv\Scripts\python.exe run_flow.py --headed
.\.venv\Scripts\python.exe run_flow.py --no-sql
.\.venv\Scripts\python.exe run_flow.py --no-transform
```

Saved SQL, transformations and browser mode apply by default. While the Flow runs, each progress event is echoed to the console (stderr) as `[stage] message`; `--quiet` silences that echo, and the JSON result on stdout is unchanged. A failure prints the full Python traceback with this file's line numbers before the one-line summary. `--no-sql` and `--no-transform` skip those operations for one run. `--no-sql` also skips the saved **Refresh materialized views** step, whose frozen list `--dry-run` prints as `refresh_views`; the script never rediscovers views (dynamic discovery is an app-run capability). Each view refreshes in its own transaction after the SQL commit and the script records a local checkpoint in `Scripts/standalone-logs/view-refresh-checkpoint.json`; if a view fails, rerun with `--retry-views` to refresh only the unfinished views without downloading, transforming or inserting again. `--headed`/`--headless` override browser mode. Run `--help` for method-specific options. The saved **Email the final file** step runs only inside Metronome, where Outlook on the BI desktop sends it; generated scripts never email and carry no recipient addresses. Recorded scripts additionally support `--output-root`, `--profile-dir` and `--parameter NAME=VALUE`; catalog/file/Outlook scripts use the saved paths. To move them, preserve the original share paths or maintain a separate copy of the script with reviewed configuration changes.

Schedule metadata is documentation; the Python script runs **once**. If Metronome is unavailable, create a Windows Task Scheduler task using the virtual environment's Python executable as Program, the quoted full path to `run_flow.py` as its argument, and Scripts as Start in. Translate the documented Asia/Dubai schedule into the workstation timezone, including daylight-saving differences. Use the authorized account, prevent overlapping runs, and use an interactive session for headed browsers/Outlook. Disable the Metronome schedule before enabling a replacement task to avoid duplicate jobs.

Period windows and catalog filters are frozen at synchronization time. They do not advance automatically without Metronome. Inspect `FLOW` in the Python script before future manual/scheduled runs; a maintainer must adjust frozen periods in a separate copy when needed. Recorded relative-date parameters resolve at execution. Standalone execution does not update server history, receipts, schedules or retention, and performs no retention deletion; operators maintain its output files separately.

## Troubleshooting a Flow in Python

`run_flow.py` is the complete Flow, start to finish, and is safe to edit. Run it, read the traceback (its line numbers refer to this file), change the code in the relevant `# ==== module: NAME ====` section, and run it again; an AI assistant can work on the file directly because every declaration is ordinary Python, not text inside a string. When the fix works, copy the changed function back into `app/NAME.py` in the Metronome repository under the same name and merge it to `main`. After the application is updated, every Flow's `run_flow.py` is regenerated with that code at startup and on the next save, so a fix made once applies to all Flows. Metronome never keeps a hand-edited file as the running version: on the next save the edited copy is archived under `Scripts/versions` as `run_flow-<hash>-edited-<UTC time>.py`, and `run_flow.py` is refreshed from the saved Flow and the current application code. Keep experiments in that archive or in a renamed copy; a normal run of an edited file still publishes to the saved destination and still needs the caller's credentials.

## Incomplete work and recovery

An unfinished or incompatible recording still has a Python file and complete descriptive JSON. Its script exits with an explicit draft explanation instead of running old settings. Finish the recording, then either test it or explicitly confirm **Save without testing** to obtain an executable version. The active and latest draft definitions are identified separately; a new draft does not replace the activated recording used for execution until the Flow is saved.

Open **More → Flow files** to check synchronization; **Open folder** takes you to the files. If a share is unavailable, the database save remains successful and the UI reports that the files need attention. Restore access and save again, or restart Metronome to reconcile. Never assume a file with an old timestamp is current after a failed synchronization. If `run_flow.py` was edited manually, the status reads `modified` until the next save, which archives the edited copy under `Scripts/versions` and refreshes the script; the save confirmation names the archived file.

Offline runs write JSONL logs under `Scripts/standalone-logs`. Outputs follow the saved stable-file or run-folder policy. Normal runs recheck path containment and folder ownership. A separate `.metronome/standalone-profile` holds the operator's session. Process locks protect the Flow/output/SQL target; do not remove locks to bypass another run. All participants need access to the same lock directory (`%ProgramData%/Metronome/execution-locks`, or a consistently configured `DG_FLOW_LOCK_ROOT`).

If SQL may have committed before interruption, inspect the target before rerunning an append. Recorded scripts leave `sql-outcome.json` for unresolved SQL outcomes; reconcile it before retrying. Preserve the failed logs and the exact script revision for the maintainer. Do not share credentials, raw report contents or unredacted logs publicly.

## Team handover checklist

Keep the managed folder and its permissions backed up. Assign an owner/contact in Metronome. Have another team member install the libraries, complete dry-run, authenticate under their own account, then run a dedicated test Flow against a safe destination. Record the date, script hash, output row checks and any extra transformation dependencies. A synthetic test or dry-run alone does not prove that the live portal, corporate authentication or SQL access works for that person.
