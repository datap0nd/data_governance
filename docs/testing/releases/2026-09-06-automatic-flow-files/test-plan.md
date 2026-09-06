# Automatic Flow files: test plan

Baseline: `a264b20dba9365eb8a0cd675977d1395c8fa762c`. Update the app from the merged release; existing managed Flows reconcile at startup. The database remains authoritative. [Results](test-report.md).

Use a dedicated fictional CSV and a test Flow with a separate managed destination. Preserve existing scripts and settings. Manual execution publishes output: do not use production SQL while rehearsing. Install Python 3.11+ and the generated Scripts/requirements.txt. Portal/Outlook cases require the authorized work-PC account.

| ID | Actions | Expected result/evidence |
| --- | --- | --- |
| HF-01 | Create/save a local CSV Flow. Open More → Flow files, then Open folder. | No Generate standalone action. flow.json has configuration, owner/creator, source/report, schedule/timezone, filters, output, transform/SQL and timing metadata. Scripts contains run_flow.py, README.md and requirements.txt. Capture status and script hash. |
| HF-02 | Rename the Flow, change owner inline, change schedule, enable then pause. Edit the related source/report and shared recording wait. | JSON and Python update automatically after each committed configuration change. Owner contact and schedule match the editor. No export action; no database schema/import change. |
| HF-03 | Create a recorded draft, save actions, test and activate. Save another draft. | A Python file exists even before validation and explicitly refuses incomplete execution. JSON distinguishes active/latest draft definitions. With an active recording, its runnable script continues to use the activated definition while JSON preserves the new draft. |
| HF-04 | Copy run_flow.py to an empty directory; run with `python -I run_flow.py --dry-run`, then execute the dedicated file Flow. Stop Metronome first for a live rehearsal. | No installed app/API/DB required; synthetic rows published successfully; server history unchanged. Record script hash and output row count. Catalog/Outlook scripts include execution code; Outlook helper is embedded. |
| HF-05 | Make run_flow.py unavailable/modified in the test folder; save again. Restore access or rename the modified copy and save/restart. | DB save survives; Flow files reports error/modified. No user edits overwritten. Missing generated script is recreated; startup/save restores current state. Keep the preserved manual copy. |
| HF-06 | Roll back a DB transaction changing the Flow; perform read-only queries. Upgrade an old installed-code launcher. | Rollbacks do not change files. Old launcher is archived and replaced by embedded execution. Windows can immediately close/delete test DBs; no leaked connection. |
| HF-07 | Open `/static/recording-preview/files.html`; save name/owner, inspect Flow files/Open folder/Close, simulate unavailable share, restore and save. | Same minimal More menu. Clear status; edits survive failure/recovery. No generation button or horizontal overflow. Collect DOM/viewport evidence. |
| HF-08 (live) | A second team member follows Scripts/README.md: dependency setup, dry-run, interactive portal or Outlook run on a test source, output inspection, optional safe SQL handoff. | Their own authentication/access works. Record Windows/Python/browser/app revision, script hash, source method, rows, log reference. Synthetic tests do not substitute for this check. |
| HF-09 (live) | Rehearse Task Scheduler against the dedicated Flow with Metronome schedule paused. Convert documented Dubai time to workstation timezone. | Exactly one run per trigger; saved output policy maintained. Verify frozen catalog periods before future execution. Restore/disable the rehearsal task afterward. |

Automated commands from repository root:

```powershell
python -m pytest tests/test_flow_handover.py tests/test_flow_standalone.py tests/test_auto_update.py -q
python -m pytest tests -q
$suites = @(rg --files tests -g 'test_*.mjs')
foreach ($suite in $suites) { node $suite; if ($LASTEXITCODE -ne 0) { throw $suite } }
node --check app/static/app.js
git diff --check
```

On this Windows ARM64 host, run pytest with the documented report wrapper to disable only pytest's optional current-directory symlink; application filesystem guards stay enabled. Required CI runs the full suite and frontend checks on Windows and Linux on the final PR head.

Accept automated checks separately from live HF-08/09. Preserve protected evidence without credentials/report content in Git. Disable test schedules; remove only explicitly identified fixture outputs. Roll back code through the normal main deployment path if needed; keep generated versions and the authoritative database. Older installed-code launchers require compatible app code, while newly generated scripts contain their execution code.
