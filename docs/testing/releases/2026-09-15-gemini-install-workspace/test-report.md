# Gemini install recovery and work folders: test report

- Plan: [test-plan.md](test-plan.md)
- PR: see the PR linking this release package; its Testing section records final CI.
- Evidence cutoff: 2026-09-15, before PR CI submission.
- Tested implementation: `c281c5e71a4e288b6dde569058f81e2ca45d28c7`, based on
  `7629b556e79117c29cc6de05060ac660c993b983`. Local tests ran on the corresponding
  uncommitted files, then those exact implementations were committed. The custom
  home case was added and run separately afterward; existing cases were unchanged.
- Environment: Windows ARM64, Node v24.19.0, PowerShell 7.6.5; Codex in-app
  browser, fictional localhost preview only.
- Finding: focused automated checks passed; generated work has explicit fixed
  paths and incomplete registration recovery preserves fixture settings.

## Executed checks

| IDs | Actual command/procedure | Result |
| --- | --- | --- |
| I1-I5 | `node --test integrations/metronome-gemini/test/setup.test.mjs` before adding I6 | 4 tests passed; includes 16 isolated PowerShell journeys, 1.97s; no skips/warnings. |
| W1-W3 | `node --test integrations/metronome-gemini/test/workspace.test.mjs` | 3 tests passed, 0.92s; no skips/warnings. Actual SDK calls and temporary filesystem operations. |
| I6 | `node --test --test-name-pattern="custom Gemini home" integrations/metronome-gemini/test/setup.test.mjs` | 1 test passed, 0.13s; no skips/warnings. Existing cases were not repeated. |
| Syntax | `node --check` on workspace.mjs, server.mjs, settings.mjs and browser.mjs | Passed, no output/errors. |
| Syntax | PowerShell `Parser::ParseFile` on install.ps1, setup-functions.ps1 and test/setup-fixture.ps1 | Passed, zero parse errors. |
| Diff | `git diff --check`, then `git diff --cached --check` | Passed. Git emitted normal LF-to-CRLF checkout notices; no whitespace errors. |
| U1 | Actual CUA browser walkthrough of docs/previews/gemini-setup.html, tab 7 | Completed prompt sequence, blank-username recovery, SQL skip, offline message, reinstall failure/retry, SQL interruption/retry and blocked-workspace copy. |

Local test evidence is reproducible from the checked-in fixtures and recorded
commands. No production account/configuration was supplied to these fixtures.

## Usability evidence and correction

The preview reuses application CSS and Outfit. Screenshot inspection showed
readable labels, wrapped recovery buttons, inline feedback and visible scratch
versus reports instructions. The first walkthrough found that a failure after
completion retained the old “Setup finished” heading. This copy was corrected
to “Installation interrupted” / “SQL setup interrupted”, reloaded, and both
failure/retry paths were walked again. The server field remained preserved.

Final preview SHA256 (working-tree bytes):
`264dc5cb3f0dbfa0def8d61322db886278bd27bf6cde01d065c0e0780c31156b`.
Final AX observations showed the corrected headings, restored-registration
message, visible retry and blocked-folder instruction. These are browser
observations of the fictional preview, not actual Gemini credential prompts.
No new owner decision or setup step was introduced; backend/copy changes proceed
under DESIGN.md without a separate approval pause.

## Findings and limits

The prior setup fixture allowed installation into a directory that already
existed. The strengthened fixture now rejects that operation, matching
[Gemini's installer](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/extension-manager.ts).
Setup distinguishes absent, linked, copied and incomplete registrations, honors
the custom Gemini home, and bumps the extension version so native copied-install
updates detect the change. The owner's exact original error was not provided;
these results establish the reproduced registration conditions only.

Unknown files and foreign/malformed registrations fail closed. Recovery uses
private backups, not deletion; sensitive settings stay local. Existing scattered
owner files are not migrated. MCP evidence/proposal stores and application logic
are unchanged. Model instructions direct unrestricted Gemini shell/file writes;
they are not an OS containment guarantee.

## Merge evidence

Required final-head CI is pending at this committed cutoff. Before merging,
record the final head SHA, run URL, check outcomes and any failure/retest in the
PR Testing section. The PR's merge record supplies the merged revision.
