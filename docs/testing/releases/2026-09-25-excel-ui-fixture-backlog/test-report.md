# Excel UI fixture queues Chrome's parallel connections: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: [PR #145](https://github.com/datap0nd/data_governance/pull/145)
- Evidence cutoff (UTC): 2026-09-25 14:07
- Tested code revision: `681dce7f73b3516bcc3d3c2723c95ad6ac292873` for the fix
  (E-02 onwards). E-01 ran on `main` `3144dc5693c6cf234f442ba65bfc2c81e808438a`
  with only the new test added, uncommitted, to the unchanged fixture. This
  report, its plan and the index rows are the only later changes.
- Environment: a Linux container (`posix linux`) with the checkout-owned
  Python 3.13.12 `.venv` (Playwright 1.62.0). Browser: headless Chromium
  141.0.7390.37 from Playwright's `chromium_headless_shell-1194`, reached
  through the test's `chrome` channel by a local symlink at
  `/opt/google/chrome/chrome`, because the container has no Google Chrome.
- Overall finding: the cause reproduced deterministically before the fix, and
  every in-scope local check passed after it, on synthetic fixtures. Final-head
  CI and the post-merge Windows run are pending at the cutoff.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| E-01 | `python tools/check.py verify --test tests/test_flow_excel_ui.py::test_fixture_server_queues_a_browser_connection_burst` | `3144dc5` plus the uncommitted new test; Linux | FAIL as expected: "connection 7 of 16 was not queued: TimeoutError('timed out')"; 1 failed, 3.4 s | `.test-runs/20260925T140348752Z-707-754eadbe/result.json` |
| E-02 | The same command with the fix, before it was committed, plus `--syntax tests/test_flow_excel_ui.py` | The fix on `3144dc5`, uncommitted, identical to `681dce7`; Linux | PASS: 1 passed, 0.8 s | `.test-runs/20260925T140415862Z-739-92ca4eb3/result.json` |
| E-02, E-03 | `python tools/check.py verify --test tests/test_flow_excel_ui.py --syntax tests/test_flow_excel_ui.py` | `681dce7`; Linux, headless Chromium 141 | PASS: 9 passed (six browser journeys, two frontend contracts, E-02), no skips or warnings, 15.1 s; syntax check passed | `.test-runs/20260925T140607929Z-2158-bc8c53b0/result.json` |
| E-04 | `TMPDIR=<scratch directory outside the checkout> .venv/bin/python -m pytest "tests/test_flow_excel_ui.py::test_flow_owner_sql_assignment_is_optional_in_each_builder[file]" tests/test_flow_excel_ui.py::test_excel_failure_recovery_and_saved_choices -q -rP -p no:cacheprovider` | The fix on `3144dc5`, uncommitted, identical to `681dce7`; Linux | PASS: 2 passed. The teardown lines listed `('/api/flows/activity', 'net::ERR_ABORTED')` for one test and five Flow page API calls with `net::ERR_ABORTED` for the other; `ERR_ABORTED` is Chrome's error for a cancelled request | Local command output |
| Whitespace | `git diff --cached --check` before the fix commit | `681dce7` | PASS | Local command output |

The verifier's `result.json` files are ignored local artifacts; CI provides
the durable GitHub evidence.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Final-head CI | NOT RUN | Starts when this report is pushed | Record the `Merge ready` run URL and tested head SHA in the PR, then merge pinned to that head |
| E-05 | NOT RUN | Windows runs this file only on the push to `main` after the merge | Record the push run's Windows result in the PR |

## Findings, limitations and retests

- Original failures, all on the Windows shard 0 of `main`, each showing
  "Failed to load page / Failed to fetch" where the Excel builder should load:
  - [35580239471](https://github.com/datap0nd/data_governance/actions/runs/35580239471)
    (scheduled, `876c953`): `test_excel_failure_recovery_and_saved_choices`.
  - [35592744740](https://github.com/datap0nd/data_governance/actions/runs/35592744740)
    (push, `a3a3b62`): `test_flow_owner_sql_assignment_is_optional_in_each_builder[portal]`.
  - [36137864461](https://github.com/datap0nd/data_governance/actions/runs/36137864461)
    (push, `3144dc5`): `test_excel_failure_recovery_and_saved_choices` and
    `test_flow_owner_sql_assignment_is_optional_in_each_builder[file]`.

  The other four completed Windows runs in that period passed, and every Linux
  run passed.
- Mechanism:
  - `renderFlows` loads ten API payloads with `Promise.all`, and the app shell
    loads twelve files. The fixture answers in HTTP/1.0 and closes each
    connection, so every request opens a new connection, six at a time.
  - The server's accept loop is one Python thread among many. While it waits,
    new connections queue. On Windows a queue of five refuses the sixth, and
    one refused API call rejects the page's `Promise.all` with "Failed to
    fetch".
  - In the latest failure, the server also logged six `WinError 10053`
    aborts: connections Chrome closed before their response. That is what a
    navigation does to the previous page's requests; E-04 shows such
    cancellations locally. Logging them is one likely cause of a busy accept
    loop at the moment the next page opens its connections. It is an
    inference, not something the logs show.
  - Linux queues one more connection than the backlog and drops rather than
    refuses extra ones, so a single browser never overflows it there. That is
    why E-01 needs a burst above six to fail on Linux.
- The Windows refusal cannot be reproduced in this Linux container. E-01
  reproduces the same undersized queue deterministically. E-05 is the first
  Windows run of the fix; one pass cannot prove an intermittent failure is
  gone, so the scheduled Windows runs keep checking. If it recurs, the
  teardown line now names the failed request and its network error.
- The local browser is Chromium 141 through a symlink; CI uses Google Chrome
  (155.0.8059.12 in the failing Windows run). Playwright's full Chromium build
  did not start in this container, so the headless shell build was used.
- `tests/test_users_browser.py` also serves the app shell from a
  `ThreadingHTTPServer` with the default backlog. It has not failed this way,
  so it is unchanged here; the same one-line server class applies if it does.

## Merge evidence

Pending at the cutoff. The final `Merge ready` run URL and tested head SHA go in
the PR before the head-pinned merge, and the post-merge Windows result (E-05)
follows there. The PR's merge record supplies the merge SHA. Nothing is
deployed; only a test file changed.
