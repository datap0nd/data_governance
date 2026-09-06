# Automatic Flow files: test report

[Plan](test-plan.md). Evidence cutoff: implementation checks in progress, 2026-09-06 UTC. Code baseline `a264b20dba9365eb8a0cd675977d1395c8fa762c` plus the changes in this PR; final code SHA and completed suite results will be recorded before merge.

Environment: Windows 11 ARM64, Python 3.13.15, pytest 8.3.5, Playwright 1.62.0. All 22 Node suites and `node --check app/static/app.js` passed. Targeted handover tests: 11 passed in 5.57s. Auto-update lifecycle plus earlier 9 handover tests: 72 passed, 1 pre-existing Starlette/httpx deprecation warning, 60.82s.

An initial full run was stopped after two setup/teardown errors. Isolation produced 80 passed and one Windows DB-file teardown error in 11.52s: the read-only synchronization connection was not closed. The code now closes it explicitly; the 72-test retest above passed. An intermediate quiet run was interrupted while investigating output buffering; it is not counted as a completed test run.

Browser preview HF-07: saved a renamed Flow, opened Flow files and folder listing, closed it, simulated unavailable share, inspected failure status, restored access and saved again. Edits were retained. Final DOM showed only Flow files/Open folder/Save Flow/Close folder preview buttons, current status and unchanged entered name. Viewport and document scroll width both 655px. This is a fictional preview, not live filesystem evidence.

Full local suite and final-head Windows/Linux CI: PENDING. Final CI URL, tested head SHA and actual results will be entered in the PR before merge. No passing result is claimed for unfinished checks.

Live HF-08 (work-PC portal/Outlook authentication, second operator, network share, SQL) and HF-09 (Task Scheduler): **NOT RUN**, because no live work-PC execution was performed in this task. Follow the plan after updating. Files contain frozen catalog periods; external dependencies/access are still required. Incomplete recordings produce an explicit draft script, not an executable validated Flow.
