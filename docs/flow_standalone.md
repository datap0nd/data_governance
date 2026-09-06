# Running a saved Flow without Metronome

Every managed Flow automatically maintains these files after committed changes:

- `flow.json` in the Flow folder: name, owner/contact, creator, schedule and Dubai timezone, source and bookmark/report route, saved filters, output policy, transformation and SQL settings, browser/timing defaults, and active/latest recording definitions.
- `Scripts/run_flow.py`: Python execution code and frozen configuration. Catalog/bookmark, local-file and Outlook Flows now include their execution code as well as recorded Flows. No installed Metronome application or running server is required.
- `Scripts/README.md` (this guide) and `Scripts/requirements.txt`: operator instructions and execution libraries.
- `Scripts/versions/`: prior generated executable revisions and previous documentation/requirements copies. Existing operator notes and custom dependency lists are archived before those generated files refresh. Preserve the whole Flow folder, including transformation scripts; consult archived notes and dependency lists when handing over.

There is no Generate standalone step. Save/edit normally; owner, schedule, source/catalog, recording and shared preference changes also refresh the derived files. Startup reconciles existing managed Flows. SQLite remains the source of truth: editing these files does not edit or schedule a Flow in Metronome. `configuration` in flow.json describes the saved Flow; `handover.state` indicates whether its script is current, draft, or failed to update. These are continuity copies, not a database restore format or a backup of run history/data.

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

Saved SQL, transformations and browser mode apply by default. `--no-sql` and `--no-transform` skip those operations for one run. `--headed`/`--headless` override browser mode. Run `--help` for method-specific options. Recorded scripts additionally support `--output-root`, `--profile-dir` and `--parameter NAME=VALUE`; catalog/file/Outlook scripts use the saved paths. To move them, preserve the original share paths or maintain a separate copy of the script with reviewed configuration changes.

Schedule metadata is documentation; the Python script runs **once**. If Metronome is unavailable, create a Windows Task Scheduler task using the virtual environment's Python executable as Program, the quoted full path to `run_flow.py` as its argument, and Scripts as Start in. Translate the documented Asia/Dubai schedule into the workstation timezone, including daylight-saving differences. Use the authorized account, prevent overlapping runs, and use an interactive session for headed browsers/Outlook. Disable the Metronome schedule before enabling a replacement task to avoid duplicate jobs.

Period windows and catalog filters are frozen at synchronization time. They do not advance automatically without Metronome. Inspect `FLOW` in the Python script before future manual/scheduled runs; a maintainer must adjust frozen periods in a separate copy when needed. Recorded relative-date parameters resolve at execution. Standalone execution does not update server history, receipts, schedules or retention, and performs no retention deletion; operators maintain its output files separately.

## Incomplete work and recovery

An unfinished or incompatible recording still has a Python file and complete descriptive JSON. Its script exits with an explicit draft explanation instead of running old settings. Finish/test the recording and save it to obtain an executable version. The active and latest draft definitions are identified separately; a new draft does not replace the activated recording used for execution.

Open **More → Flow files** to check synchronization; **Open folder** takes you to the files. If a share is unavailable, the database save remains successful and the UI reports that the files need attention. Restore access and save again, or restart Metronome to reconcile. Never assume a file with an old timestamp is current after a failed synchronization. If `run_flow.py` was edited manually, preserve/rename it and save the Flow again; Metronome will not overwrite those edits.

Offline runs write JSONL logs under `Scripts/standalone-logs`. Outputs follow the saved stable-file or run-folder policy. Normal runs recheck path containment and folder ownership. A separate `.metronome/standalone-profile` holds the operator's session. Process locks protect the Flow/output/SQL target; do not remove locks to bypass another run. All participants need access to the same lock directory (`%ProgramData%/Metronome/execution-locks`, or a consistently configured `DG_FLOW_LOCK_ROOT`).

If SQL may have committed before interruption, inspect the target before rerunning an append. Recorded scripts leave `sql-outcome.json` for unresolved SQL outcomes; reconcile it before retrying. Preserve the failed logs and the exact script revision for the maintainer. Do not share credentials, raw report contents or unredacted logs publicly.

## Team handover checklist

Keep the managed folder and its permissions backed up. Assign an owner/contact in Metronome. Have another team member install the libraries, complete dry-run, authenticate under their own account, then run a dedicated test Flow against a safe destination. Record the date, script hash, output row checks and any extra transformation dependencies. A synthetic test or dry-run alone does not prove that the live portal, corporate authentication or SQL access works for that person.
