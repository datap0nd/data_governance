# 2026-11-11: Tests that prove nothing removed, with the leftover code they kept alive: test plan

- Change/PR: link in the PR's testing section. A review of all 3,073 tests
  found about 180 that do no work; 172 are removed here. The owner approved removing three groups:
  the weak tests (tests that only read source text, tests of features that no
  longer exist, tests of code nothing calls together with that code, and exact
  duplicates); the leftover Flow groups code and its tests; and two prototype
  pages and their tests.
- Code baseline: `main` at `eed4930`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv`; required final-head CI for the full regression, including the
  browser tests that need Microsoft Edge or the bundled headless browser

## Prerequisites and test data

No live system. Every test uses a temporary SQLite database and fictional
portals, people and files. Browser tests drive synthetic pages served from the
test itself.

## What is removed

| Group | Removed | Kept on purpose |
| --- | --- | --- |
| Tests that only read source text | Assertions that a string appears in `app.js`, `setup.ps1`, a worker source file or a workflow file | Four tripwires in `tests/test_flows.py` that guard something no behavioural test can reach (setup never purges local files, the retention module is the only deletion site, setup downloads before stopping services, the ASAP scraper never sends control-modified clicks), and the log-rotation contract |
| Tests of removed features | `tests/test_overview_removed.py` and similar checks that a page deleted long ago is still absent | The behavioural halves of those files |
| Dead code and its tests | `_fetch_tests_workflow_gate`, `_tests_gate`, `_tests_gate_for_status`, `_tests_gate_auto_status` and two constants in `app/main.py`; nothing called them | `_tests_gate_record` and what the status endpoint still uses |
| Exact duplicates | A parametrised case or test whose path another test already runs with the same assertions | Any case that asserts one thing its twin does not |
| Flow groups | `app/routers/flow_groups.py` (five `/api/flows/groups` routes), its three calls from `app/routers/flows.py`, its entry in the update drain list, the group name in the Data Auditor's Flow list, `tests/test_flow_topic_groups.py` | The `flow_groups` and `flow_group_members` tables, their triggers and every stored row: no migration, no `DROP`, no `DELETE` |
| Prototype pages | `groups.html/.js/.css` and `python-folder.html/.js` under `app/static/recording-preview/`, with their two browser tests | Every other preview page |

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| P-01 | Collect the whole suite | No import or collection error; 2,924 tests (3,096 before) | `python -m pytest --collect-only -q` |
| P-02 | Run every test file this change edits, plus the Data Auditor and sharding files | All pass except cases that need a browser this container cannot start | The `python tools/check.py verify` command below |
| P-03 | Run the edited browser files directly with real Chrome | All pass except the Edge-only case | `python -m pytest tests/test_flow_excel_ui.py tests/test_flow_list_browser.py tests/test_recording_optional_checks.py tests/test_flow_recordings.py` |
| P-04 | `import app.main`; search the tree for every removed name | Imports; no reference left | Console output |
| P-05 | `GET /api/flows` and the Flows list with a Flow that sits in an old saved group | The Flow is listed with the rest of its source | `tests/test_flow_list_browser.py` |
| P-06 | Start an update drain and post a Flow run | 503 with `Retry-After`; worker progress routes stay open | `tests/test_auto_update.py::test_update_drain_middleware_blocks_new_work_but_not_worker_progress` |
| P-07 | Data Auditor catalog with a restricted reader | The catalog lists Flows by name | `tests/test_data_auditor.py`, `tests/test_auditor_manifest.py`, `tests/test_auditor_lifecycle.py` |
| P-08 | Partition the suite into six shards with the refreshed durations | Every test in exactly one shard; recorded seconds per shard within a few seconds of each other | `tests/test_ci_sharding.py` and the prediction in the report |

## Automated checks

```bash
python tools/check.py verify \
  $(git diff --name-only --diff-filter=M origin/main -- tests | sed 's/^/--test /') \
  --test tests/test_data_auditor.py --test tests/test_auditor_manifest.py \
  --test tests/test_auditor_lifecycle.py --test tests/test_ci_sharding.py \
  --syntax app/main.py --syntax app/routers/flows.py --syntax app/auditor/router.py
```

Required final-head CI is the full regression.

## Acceptance and cleanup

Accepted when P-01 to P-08 pass, with browser cases that cannot start locally
passing in final-head CI, and `Merge ready` passes on the final head.

Rollback is a revert of the merge commit. The database is not changed by this
release, so nothing has to be restored.
