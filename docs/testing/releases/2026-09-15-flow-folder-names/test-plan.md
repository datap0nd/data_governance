# Flow folder names and save-time Python refresh: test plan

- Change/PR: [PR #126](https://github.com/datap0nd/data_governance/pull/126).

- Scope: name-only Flow folders, rename on Save, preservation/recovery, and generated Python matching committed settings and transformation bytes.
- Baseline: `b39c8193` (current origin/main when work began).
- Related report: [test-report.md](test-report.md).
- Environments: isolated Windows Python fixtures and fictional browser preview; final PR CI supplies the full Python regression.

## Prerequisites and test data

Use Python 3.13 with the repository test dependencies (`requirements-ci.lock`).
Use a new temporary root outside the checkout. All database rows, CSVs, scripts,
folder names and report routes in the automated checks are synthetic. No shared
folder settings or existing Flow data are needed. Tests create their own database.

## Cases

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| F-01 | Create portal, Outlook and Local fixture Flows. Inspect the Flow directory. Try reserved names, punctuation, long names and case-insensitive collisions. | Folder uses the sanitized name without an appended ID. Ownership remains in flow.json. A conflicting folder and its contents are preserved. Record assertions and directory names. |
| F-02 | Rename a saved Flow; include a case-only rename. Save an existing ID-suffixed folder without changing its name. | Folder moves on Save; no old directory remains. Downloads, Scripts, manifest, versions and user files are preserved. Returned paths and current Python reference the new directory. |
| F-03 | Seed a completed run and its files, then rename its Flow with a managed transformation. | Historical file locations and future job paths point at the moved files; private artifacts and original source script remain unchanged. Frozen historical Flow name stays historical. |
| F-04 | Simulate an occupied destination, denied move, failed database commit process exit between move and commit, and an outstanding publication journal. Retry Save after resolving the condition. | Failed saves preserve prior settings/files and allow retry. The interrupted move is reconciled from ownership evidence. No occupied directory is overwritten, even if empty. |
| F-05 | Queue/claim/run a synthetic job, or hold the standalone Flow lock; attempt rename. | Save rejects the conflict before moving the folder. Retry after the run/lock ends. |
| F-06 | Set an isolated legacy Flow to unmanaged, occupy its old name with a fictional user folder, then Save with a free new name. | Allocate the new name, preserve the occupied old folder and generate current Python. Existing legacy retry/adoption behavior stays intact. |
| P-01 | Save a Local Flow with a new name and CSV input. Immediately inspect run_flow.py and run it with the fixture interpreter in isolation (`-I`). | New configuration is present before Save returns. Generated Python succeeds using synthetic input without a Metronome server. |
| P-02 | Enable a transformation; edit its contents at the same saved path; save unchanged Flow settings. | Generated Python and its snapshot hash change to include the new transformation. |
| P-03 | Save a recording revision, select it on Flow Save, then save/select an edited revision and rename. | Current generated Python contains the newly selected recording without requiring a recording execution. |
| P-04 | Run the existing handover checks for owner/schedule/shared changes, drafts, generator failure/retry, edited script archival, missing files and secret omission. | Appropriate current/draft/error status; refresh/recovery preserves user edits and excludes credentials. |
| U-01 | Serve `app` at localhost:8769. Open `/static/recording-preview/folder-names.html`. Rename to Weekly orders and Save; use Edit flow to reopen. | Fictional tree changes from the legacy ID path to Weekly orders; Python revision changes and Flow saved is visible. |
| U-02 | Select Folder name already exists or Folder unavailable; enter Monthly orders and Save. Switch to Normal save and retry. | Error beside Save explains next action; form preserves Monthly orders; tree remains unchanged until successful retry. |
| U-03 | Select Python refresh fails, rename and Save; then Edit flow, select Normal save and Save again. | Save reports files could not update; displayed Python remains at its old revision/settings until retry succeeds. |

## Automated checks

Smallest affected set:

```powershell
python -m pytest tests/test_flow_folder_rename.py tests/test_flow_layout.py tests/test_flow_handover.py -q --basetemp=<fresh-external-temp-root> --junitxml=<external-evidence-file>
```

Syntax: parse the changed Python modules/tests with `ast.parse`; run
`node --check app/static/recording-preview/folder-names.js`; run `git diff --check`.
On this workstation, pytest's optional `current` symlink aliases need to be disabled
in the runner because Windows rejects following them; the report records the exact
wrapper. Application symlink checks are not disabled. Final-head CI uses ordinary
pytest and provides Linux regression and Windows verifier contracts.

## Acceptance and cleanup

Accept after the affected checks and final-head required CI pass. Owner approved
the demonstrated journey in this task on 2026-09-15. Keep fictional preview evidence
and test aggregates. Test schedules/database/files are isolated; close the temporary
preview server after review. Remove only explicitly named fixture roots when no
longer needed. Never remove actual Flow folders as cleanup.

For operational recovery, restore access and retry Save. Existing ID folders migrate
on their next successful Save. External consumers of the old path must be updated.
Reverting code does not move renamed folders back; use the saved database paths and
ownership manifests, preserving all contents.
