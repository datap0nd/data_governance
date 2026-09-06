# Recording worker startup: test report

- Plan: [test-plan.md](test-plan.md)
- Evidence cutoff: 2026-09-06, before the implementation commit.
- Revision: working changes based on `00839926f70bf594bdb579bd24371d4972c920e4`.
  The PR testing section records the final commit and CI evidence produced
  after this report's cutoff.
- Environment: Windows ARM64, Python 3.13.15, Node 24.19.0, Playwright 1.62.0,
  Chrome 152.0.7977.76. Browser fixtures use fictional data and headless Chrome.

## Reproduced defect

Before this patch, `python -I app/flow_worker.py --help` failed immediately with
`ModuleNotFoundError: No module named 'app'` at the `app.flow_clock` import.
That import preceded the direct-file path bootstrap. The installed headed task
uses embedded Python to execute this file directly. The failure happens before
worker registration and before the worker's diagnostic logger is configured.

The isolated probe passes after moving the import. It only parses `--help`;
neither attempt starts a worker, browser or portal session. This confirms the
code defect, not its occurrence on the owner's deployed work PC.

## Executed checks

| Check | Result |
| --- | --- |
| Isolated direct entry point from outside the checkout | PASS; exit zero, worker options shown, no profile files created |
| `tests/test_recording_startup.py` | 14 passed in 9.38s; timeout/retry, skipped/error launch, capacity waits, disabled slot, fresh registration, claim races and preserved actions |
| `tests/test_recording_startup_ui.py` | 2 passed in 24.23s; real test/save/cancel buttons, polling, failure feedback, retry, retained edits and narrow layout |
| Related recording/controls/capacity/journey regressions | 70 passed in 97.95s, one Starlette TestClient deprecation warning; this run preceded the final browser-launch-failure parameter added to the control test |
| All 22 `tests/test_*.mjs` suites | PASS |
| Changed JavaScript syntax and `git diff --check` | PASS; Git reports ordinary LF-to-CRLF checkout notices |
| Full Python suite | 1578 passed, 10 Starlette deprecation warnings in 415.20s; includes the final browser-launch-failure case and all new startup/browser regressions |
| Final-head Windows/Linux CI | PENDING at committed report cutoff; exact head SHA, run links and results must be recorded in the PR before merge |

Focused and full Python commands invoke `pytest.main` with `-q --tb=short` and
an external `tempfile.mkdtemp(prefix='metronome-...-')` as `--basetemp`. The local
Windows invocation disables `_pytest.pathlib._force_symlink` for temporary
directories. CI uses the ordinary `python -m pytest tests -q` command.
The full local run also writes a JUnit result to the OS temporary directory,
`metronome-recording-startup-full.xml`.

Node checks run every `tests/test_*.mjs` file with Node, plus `node --check` on
`app/static/app.js`, `app/static/flow_recording_editor.js`, and the startup
preview JavaScript.

## Browser and review evidence

The [clickable preview](../../../../app/static/recording-preview/startup.html)
uses the production recording editor with substituted APIs. This release
corrects existing status wording and recovery behavior; it adds no production
configuration or alternate recording journey.

- [Waiting for worker](evidence/waiting.png)
- [Opening browser](evidence/opening.png)
- [Failed startup with retry available](evidence/failed.png)
- [Failure at 390px](evidence/failed-mobile.png)
- [Cancellation at 390px](evidence/cancelled-mobile.png)

The checks exercised editing an action, saving it through Test recording,
queued/claimed/running progress, failure and retry, cancellation with stale
progress, missing error detail, and back/reopen navigation. Recorded actions
and the edited label remained intact; schedules stayed disabled. Screenshots
were visually inspected at 1280×960 and 390×844. Neither width overflowed.

Independent code review found and resolved a disabled-slot wait that could
otherwise remain queued indefinitely. Final review found no merge blockers.

## Unperformed checks

Actual work-PC scheduled-task startup, worker registration, GSCM/ASAP sign-in
and live recording validation, and UNC share permissions: **NOT RUN**. No live
app database or worker runtime is available on this host; its local server
serves only the fictional preview. Follow START-2 after updating app and worker
from main. Synthetic tests do not prove live portal or share success.
