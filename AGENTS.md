# Working in this repository

Metronome is an internal FastAPI + vanilla JavaScript web application for BI
operations: report and source monitoring, ownership, lineage, and scheduled
"Flows" that download portal reports, transform them and publish to SQL. The
owner tests only what is merged to GitHub `main`. This file is the one place
where working rules live; the documents it points to hold the procedures.

## Done means

A code change is finished when all of the following are true:

1. It is merged to `main` at the head that CI tested, with the `Merge ready`
   check green and its run URL and head SHA recorded in the PR.
2. A release test plan and report exist under `docs/testing/releases/` and are
   linked from the PR.
3. The reply opens by naming one delivery state: implementation ready, local
   checks complete, CI complete, or merged. A merge is a merge; the owner
   deploys separately.

Continue until the change is merged. Standing owner authorization covers
branching, opening the PR, and the head-pinned merge, so none of those steps
waits for confirmation. If the merge cannot happen (failing checks, a conflict,
a denied permission), say so in the first sentence of the reply. Questions,
analysis and advice do not trigger this workflow.

## Precedence and authority

The owner's current task instruction takes precedence over this file, except
for the invariants below, which change only when the owner edits this file.
When a repository file makes you stop, skip a step or change approach, name the
file and quote the line in your reply so the instruction can be fixed.

Approval is needed before changing branch protection, workflow secrets,
production data, or any live system (work PC, portals, service accounts).
Everything else in the checkout is yours to change. Scratch output belongs in
the ignored `.test-runs/` directory or the session's scratch area.

## Invariants

- PostgreSQL credentials configured for Metronome are read-only. Probes and
  ownership scans use SELECT only; the code never issues writes or DDL.
- Live checks against the work PC, portals, authentication or hardware are
  opt-in: perform, plan or report them only when the owner asks for them in
  the current task. Otherwise leave them out of plans, reports and replies
  entirely. Never invoke a Metronome live-fix skill for testing unless the
  owner names it in the task.
- Never weaken, skip or quarantine an existing test to make a change pass.
- Never commit credentials, cookies, private portal URLs, report data or raw
  traces. Reference protected evidence by an opaque identifier.
- Schedules use Dubai wall-clock time and monitoring timestamps stay UTC;
  scheduled communication fails closed when its saved Power BI page, visual or
  column disappears.

## Codebase map and vocabulary

- `app/main.py`, `app/routers/*.py`: FastAPI application and API routes.
- `app/static/app.js` and `app/static/*.css`: the single-page UI, no build step.
- `app/flow_*.py`: Flows, the largest subsystem. Recording and replay of the
  ASAP and GSCM portals (`flow_recording*.py`, `flow_gscm*.py`), the worker
  and its capacity (`flow_worker.py`, `flow_parallel*.py`), managed folders
  and paths (`flow_paths.py`, `flow_layout.py`), SQL publication and view
  refresh (`flow_sql.py`, `flow_view_refresh*.py`), and per-Flow portable
  scripts (`flow_portable.py`, `flow_standalone.py`).
- `app/scanner/`, `app/checks/`: Power BI report scanning and quality checks.
- `app/ai/`: the bounded read-only Operations Investigator.
- `tests/test_*.py` (pytest) and `tests/test_*.mjs` (node contract tests).
- `tools/check.ps1` and `tools/check.py`: local verification; `tools/ci/`:
  the CI merge gate; `setup.ps1`: the Windows installer and updater.

Vocabulary: a **Flow** is a scheduled acquisition (portal download, Outlook
attachment or local file) with optional transformation and SQL publication; a
**Recording** is a captured browser session the worker replays; a
**Pipeline** chains Flow runs and view refreshes; **ASAP** and **GSCM** are the
two portals Flows automate; **NASCA** wraps protected Excel downloads that are
opened through desktop Excel.

## Read what the task needs

- `DESIGN.md` before changing any screen; `PRODUCT.md` for product intent.
- `docs/testing/README.md` when writing the release test plan and report.
- `docs/flow_paths.md`, `docs/recorded_flows.md`, `docs/flow_standalone.md`
  and the other `docs/flow_*.md` files when touching Flows.
- `README.md` for operator setup, environment variables and services.
- `docs/archive/` holds superseded plans and handoffs; nothing there is
  current guidance.

## Verification

Use the checkout-owned Python 3.13 `.venv` built from `requirements-ci.lock`:
`tools/check.ps1` on Windows, `python tools/check.py` elsewhere (the same
modes, selectors and `result.json`). Both isolate the database, temporary
files, browser profiles and the Flow root under a disposable per-run
directory with no production access, so run focused tests, fix failures caused
by the requested change, and rerun the affected cases without asking at each
step.

Run one smallest non-overlapping affected test set plus syntax checks for the
files you changed, and let final-head CI supply the full regression. The
reason is cost: the full Python suite takes about twelve minutes and a local
full run duplicates the evidence CI already records. A local full suite is a
diagnostic and needs a recorded reason. Reuse prior local evidence only
through the verifier's reuse option, which checks the source fingerprint.

Documentation, policy, PR-template and workflow-only changes take the
lightweight CI scope gate; application, dependency and test changes take the
full suites.

## Release test package

Every merge to `main` carries a plan and report under
`docs/testing/releases/`, scaled to the change: a documentation change gets
documentation checks. Record actual commands, revision, environment and
results. Mark in-scope automated checks that have not finished (usually
final-head CI) as pending or NOT RUN; that is correct and expected. Live
checks are absent unless requested. Identify synthetic fixtures as synthetic.
The procedure and templates are in `docs/testing/README.md`.

## Changed screens

Follow the usability review in `DESIGN.md`. Owner feedback on a clickable
preview is required before implementing a materially changed journey; wording,
spacing and backend-only changes proceed without a pause.

## Replies

Open with the delivery state and the PR link, then link the test plan and
report. Use plain prose and describe outcomes; the diff is the record of the
change. If a step was skipped or failed, say which and why.
