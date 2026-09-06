# Recording worker startup: test plan

The worker must start through the installed Windows task's direct Python entry
point. A recording test must show whether it is waiting or opening a browser,
and release its reservation after a failed start so the saved actions can be
tested again.

## Prerequisites

Use matching app and worker revisions from this PR after updating from main.
For live checks, sign in to the work-PC desktop and use a disposable recorded
Flow with harmless data. Use the configured managed folder. Do not stop tasks
that are running other Flows. Save only sanitized status and revision details.

The fictional [startup preview](../../../../app/static/recording-preview/startup.html)
uses the production recording editor and simulated APIs. Serve `app` locally
and open `/static/recording-preview/startup.html`. It cannot start a worker.

## Cases

1. **START-1 — Installed entry point.** From a directory outside the checkout,
   run `python -I <absolute-checkout-path>/app/flow_worker.py --help`. Expect exit
   zero and the worker options, including `--headed`. No worker, profile or
   browser should start. This reproduces the isolated module-search behavior
   that previously failed before the worker could register.
2. **START-2 — Test on the work PC.** In Edit Flow, open the recording and click
   Test recording. Expect waiting feedback, then Opening browser, then normal
   authentication/step progress. Confirm the visible slot registered the same
   code revision as the app. The test must not publish production output or SQL.
3. **FAIL-1 — Accepted launch, no worker.** In an isolated database fixture,
   accept the OS launch without registering/claiming a worker. After 120 seconds
   with capacity available, the session must fail with a readable retry action,
   its revision must return to draft and its actions must remain intact. Retry
   with an available worker and confirm the new session can be claimed. A late
   claim must not resurrect the expired session.
4. **FAIL-2 — Unsupported or rejected launch.** Simulate a skipped/failed launch.
   Expect a terminal error, no active reservation and a retryable draft. If the
   worker already claimed the session, the launch result must not fail it.
5. **WAIT-1 — Legitimate work ahead.** Occupy the pinned slot, global capacity or
   portal capacity, or queue an earlier recording on the same slot. Advance
   beyond the startup timeout. The queued test must remain waiting with clear
   feedback. Once capacity becomes available it receives a fresh startup window.
   Reduce visible capacity below the pinned slot: expect a retryable error,
   then retry and confirm an enabled slot is selected.
6. **UI-1 — Failure and retry.** Exercise the fictional preview at desktop and
   narrow widths. Click Test recording, observe waiting/opening feedback, fail
   startup, and retry. The button must become available and recorded actions
   remain. A cancelled or failed session must never fall back to Starting worker.
7. **REG-1 — Shared worker paths.** Run the full Python suite, all Node suites,
   and changed-JavaScript syntax checks. Cover existing recording/validation,
   reservation cancellation, browser failure, catalog work and managed paths.

## Evidence and cleanup

Record case ID, UTC time, app/worker SHA, browser, session ID, observed status and
sanitized screenshot or CI link. Live work-PC launch, GSCM/ASAP SSO, and UNC
permissions remain NOT RUN until actually exercised. Synthetic tests cannot
prove those outcomes. Remove only disposable test flows and local test files;
preserve recordings, production profiles and existing output.
