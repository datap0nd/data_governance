# Excel UI fixture queues Chrome's parallel connections: test plan

- Change/PR: [PR #145](https://github.com/datap0nd/data_governance/pull/145).
  Test infrastructure only; the application does not change. The Windows
  regression on `main` failed intermittently in `tests/test_flow_excel_ui.py`
  with "Failed to load page / Failed to fetch". That was 3 of the 7 completed
  Windows runs since 2026-09-21, and Linux runs passed. The fixture's
  `ThreadingHTTPServer` listened with socketserver's default backlog of 5.
  Chrome opens six connections at once for the app shell's files and parallel
  API calls, and Windows refuses a connection beyond the backlog. The fixture
  now listens with a backlog of 128 and prints each failed request with its
  network error.
- Code baseline: `main` `3144dc5` (PR #144 merged).
- Related report: [test-report.md](test-report.md)
- Intended environments: a Linux container with the checkout-owned Python
  3.13 `.venv` and Playwright's headless Chromium; required GitHub Actions CI
  (Ubuntu on the PR); and the Windows shards of the push run on `main` after
  the merge, which are the only CI runs of this file on Windows.

## Prerequisites and test data

The fixture's fictional in-memory Flow and run payloads; no portal, SQL
database or work PC. The browser tests launch the `chrome` channel. This
container has no Google Chrome, so a local symlink at
`/opt/google/chrome/chrome` points that channel at Playwright's headless
Chromium. CI installs Google Chrome.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| E-01 | Reproduce the cause. Add `test_fixture_server_queues_a_browser_connection_burst` to the unchanged fixture, which has socketserver's default backlog of 5, and run it. The test creates the fixture server without starting its accept loop, as when a busy runner starves that loop, then opens 16 connections with a 2-second timeout each | Fails. Linux queues backlog + 1 = 6 connections, so connection 7 times out; on Windows the queue is 5 and connection 6 is refused | Verifier `result.json` |
| E-02 | The same test with the fix (backlog 128) | All 16 connections are queued | Verifier `result.json` |
| E-03 | Every test in `tests/test_flow_excel_ui.py` at the fixed revision: the six browser journeys (worksheet recovery and saved choices; the Excel setting in the file and portal builders; the optional SQL owner in the Outlook, file and portal builders), the two frontend contracts and E-02 | All pass, with no page errors and no unexpected fixture requests | Verifier `result.json` |
| E-04 | Failed-request output: run two browser journeys with `pytest -rP` | The teardown line lists each failed request with its network error; a cancelled request shows as `net::ERR_ABORTED` | Command output |
| E-05 | The push run on `main` after the merge runs this file on `windows-latest` with Google Chrome | Windows shard passes | CI run |

## Automated checks

```bash
python tools/check.py verify --test tests/test_flow_excel_ui.py::test_fixture_server_queues_a_browser_connection_burst   # E-01 before the fix, E-02 after
python tools/check.py verify --test tests/test_flow_excel_ui.py --syntax tests/test_flow_excel_ui.py                     # E-03
```

Final-head CI on the PR runs the Ubuntu shards. E-01 is the deterministic
check of the cause on any platform. E-05 checks Windows, but one pass cannot
prove an intermittent failure is gone. The later scheduled runs keep checking,
and the new output names the failed request if it recurs.

## Acceptance and cleanup

Accepted when E-01–E-04 have recorded results and the final-head `Merge ready`
passes. The PR then merges pinned to that head, and E-05 is recorded in the
PR once the push run finishes. Nothing is deployed: only a test file changes.
Rollback: revert the PR. The `/opt/google/chrome/chrome` symlink exists only
in the disposable container.
