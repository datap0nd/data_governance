# Managed Flow editor: test report

- Plan: [test-plan.md](test-plan.md)
- Evidence cutoff: 2026-09-06, before the implementation commit.
- Revision: working changes from `1928a50166a29e07ee6e02df23457dcb2dd67b22`.
  The PR testing section records the final tested commit and CI runs after this
  report's cutoff. Screenshots below were captured with the production JS/CSS
  in this commit, using fictional data and API substitutes.
- Environment: Windows ARM64, Python 3.13.15, Node 24.19.0, Playwright 1.62.0,
  Chrome 152.0.7977.76, headless browser.

## Executed checks

| Check | Result |
| --- | --- |
| All 22 `tests/test_*.mjs` suites | PASS |
| `node --check` for app.js, flow_recordings.js, flow_recording_editor.js | PASS |
| `git diff --check` | PASS; Git reported normal LF-to-CRLF checkout notices |
| `tests/test_managed_flow_editor.py` | 3 passed in 3.10s: no catalog/destination requirement, canonical folder, automatic legacy save preserving history, real editor method/output/new-recording/failure/mobile checks |
| Enforced pending-settings test plus editor checks | 3 passed, 1 warning, 3.82s |
| Initial full Python run | 1524 passed, 33 failed, 11 warnings, 411.86s; findings and retest below |
| Retest of every initial failure (`--lf`) | 33 passed, 195 deselected, 1 warning, 19.30s |
| Final full Python run and final-head Windows/Linux CI | PENDING at this committed report cutoff; final results must be recorded in the PR before merge |

Python invocations used `pytest.main([...,'-q','--tb=short',
'--basetemp='+tempfile.mkdtemp(prefix='metronome-...-')])`, with the local
`_pytest.pathlib._force_symlink` compatibility shim disabled for Windows temp
directories. CI runs ordinary `python -m pytest tests -q` without that shim.

The full run found old fixtures advertising workers without shared-artifact
support, a nonexistent transformation script, shared custom paths that new API
creations no longer accept, and selectors using the former recording labels.
Fixtures now advertise current worker capabilities, use a real temporary script,
and explicitly seed historical data where legacy shared-folder recovery is the
subject. The separate capability-rejection tests remain intact. All failures
passed on retest. Warnings were Starlette TestClient deprecations.

## Browser evidence

- [Source: name first, only recorded controls](evidence/source.png)
- [Fixed output and managed location](evidence/output.png)
- [Date-token guidance](evidence/dated-output.png)
- [390px mobile layout](evidence/mobile.png)

The owner supplied the requested corrections to the previously demonstrated
journey. This implementation applies those corrections. The updated clickable
preview uses the production form and was opened in Codex during implementation.
Browser checks exercised method switching, retained names, review-recording
settings, both output modes, failed Save and retry, creating a recording without
a second naming dialog, Outlook/file editors, and mobile overflow (none).

## Unperformed checks

Actual work-PC GSCM/ASAP authentication and recording, access to the configured
UNC share, two-run Power BI/Excel refresh, and live retention: **NOT RUN**.
Synthetic APIs and local browser checks do not establish those outcomes. Follow
PATH-1, OUTPUT-1/2 and RECOVERY-1 in the plan after updating app and worker.

## Merge evidence

PR and final tested SHA/CI links are recorded in the PR testing section before
merge. This report deliberately does not claim results produced after its cutoff.
