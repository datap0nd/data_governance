# Gemini install recovery and work folders: test plan

Scope: extension 0.2.1, installer recovery, reporting instructions and fixed MCP
work directories. Metronome application/flow logic is unchanged. All fixture
files, connection probes and browser pages below are synthetic.

## Prerequisites and commands

Use the PR checkout, Node 22+, PowerShell 7 (`pwsh` on PATH), and the locked
extension dependencies (`npm ci --ignore-scripts --prefix integrations/metronome-gemini`).
No account settings, business workbooks or databases are fixture inputs.

```powershell
node --test integrations/metronome-gemini/test/setup.test.mjs integrations/metronome-gemini/test/workspace.test.mjs
node --check integrations/metronome-gemini/workspace.mjs
node --check integrations/metronome-gemini/server.mjs
node --check integrations/metronome-gemini/settings.mjs
node --check integrations/metronome-gemini/browser.mjs
```

Also parse install.ps1, setup-functions.ps1 and test/setup-fixture.ps1 with
`System.Management.Automation.Language.Parser::ParseFile`; require zero errors.
Run `git diff --check`. Required final-head CI supplies the full Python,
frontend, Windows and disposable PostgreSQL regressions; do not duplicate them
locally without a failure to diagnose.

## Cases and expected results

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| I1 | Run setup fixture with no registration, a link and a local copy. | Install only when absent; reuse link; update copy. Three native SQL fields in order; no URL/table-list questions. Capture mock command sequence. |
| I2 | Seed only `.env`, or a recognized copied extension with missing metadata; run setup twice. | Back up registration outside extensions, install without destination conflict, preserve settings/scopes, then update on rerun. Fixture enforces upstream's destination-exists guard. |
| I3 | Fail installation after creating a partial new registration. | Restore the original folder and settings; retain the partial attempt separately; stop before SQL. No automatic deletion. |
| I4 | Seed foreign-source metadata, malformed metadata or unknown contents. | Fail before Gemini operations; preserve original files. Trailing source separator is accepted for the same source. |
| I5 | Fail npm, stop the service probe, interrupt SQL, leave username blank, or skip server (including quoted whitespace). | Dependencies stop setup; offline service allows completion; completed fields survive interruption; blank username fails; blank server skips SQL. |
| I6 | Use a fictional custom GEMINI_CLI_HOME access file. | Read its restricted flow/site scope rather than default account settings. |
| W1 | Connect SDK client to synthetic MCP and call capabilities, then prepare_workspace twice. Write fictional script/report/source files. | Capabilities makes no directories. Preparation returns fixed absolute scratch/reports paths, preserves existing work and requires no source path argument. |
| W2 | Remove the fixture scratch directory, then prepare again. | Empty scratch is recreated; Korean report and source contents remain exact. No evidence/proposal store is removed. |
| W3 | Put a file at scratch, retry, then remove the fixture obstruction and retry. Redirect the root with a synthetic symlink/junction. | Blocked/redirected paths fail without fallback or target writes; normal-directory retry works. |
| U1 | Serve the fictional preview, complete server/username/password, retry a blank username, skip SQL, and test offline, interruption, reinstall failure and blocked workspace. | Clear inline feedback, preserved fields, separate scratch/report cleanup guidance, visible retry; no success label after failure. |

## Preview walkthrough

Serve the repository with `python -m http.server 8768 --bind 127.0.0.1` and open
`http://127.0.0.1:8768/docs/previews/gemini-setup.html`. Use fictional values only.
The same three-field journey remains; changes are backend recovery and explanatory
copy, with no new owner decision or setup step. Record screenshot/AX evidence,
revision or content hash, actual controls walked and any correction/retest.

## Cleanup and operational instructions

Fixture tests remove only their generated temporary roots. Stop the preview
server afterward. Do not run the real installer as a fixture test.

After updating from main, the existing install command remains
`.\integrations\metronome-gemini\install.ps1`; restart Gemini afterward.
`/metronome` and `/html_replicate` direct generated work to
`%USERPROFILE%\Metronome Gemini Work`. Close Gemini before cleaning `scratch`.
Keep needed `reports` and durable `.gemini` evidence/proposal receipts. Do not
automatically move or delete previously scattered checkout files.

The MCP prepares fixed directories; Gemini's own file/shell tools follow
instructions, not an OS sandbox. A successful synthetic run does not establish
that an unrestricted model will obey every instruction.
