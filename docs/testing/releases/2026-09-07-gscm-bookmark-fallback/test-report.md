# GSCM bookmark fallback navigation and import suggestion: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: [PR #83](https://github.com/datap0nd/data_governance/pull/83)
- Evidence cutoff (UTC): 2026-09-07 07:00
- Tested code revision: `b5ea0b3` (implementation commit; this documentation commit follows it without code changes)
- Environment: Linux container, Python 3.11.15, pytest with Playwright 1.62.0 and the preinstalled Chromium 1194 build aliased to the `chrome` and `msedge` channel paths because the container's network policy blocks Chrome/Edge downloads; Node 22.22.2. CI adds real Chrome/Edge on Windows and Ubuntu.
- Overall finding: automated checks PASS on `b5ea0b3`; live work-PC cases NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| FB-03 to FB-09 synthetic, activation fix, import suggestion, diagnostics, portable bundle | `python -m pytest tests/test_recording_gscm_bookmark.py -q` | `b5ea0b3`, Linux | PASS: 20 passed in 1.8 s | Local output; cases: visible row, target below from mid-position (top-first), target above the view, delayed rendering, changed ordering, collapsed ancestor → `end_confirmed`, stalled scrollbar → `scrollbar_stalled`, missing scrollbar, wrong frame/dataset unavailable, missing identity, duplicates needing ID + folder confirmation, duplicates under same-named subfolders, duplicates in one folder, cancellation via progress, deadline, diagnostics keep strategy and drop names, portable bundle contains the helper. |
| FB-01, FB-02 synthetic editor journey | `python -m pytest tests/test_flow_recordings.py -k "prefills or repairs_a_recycled" -q` | `b5ea0b3`, headless Chromium | PASS: 2 passed in 2.5 s | Suggestion hint shown for the Favorite grid path; choosing the target prefills recorded text; recycled row saves with an entered name. |
| FB-10 regression on recording, clicks, diagnostics, handover, identity, editors | `python -m pytest tests/test_flow_recordings.py tests/test_recording_diagnostics.py tests/test_recording_clicks.py tests/test_flow_handover.py tests/test_recording_identity.py tests/test_recording_visual_editor.py tests/test_managed_flow_editor.py -q` | pre-commit working tree equal to `b5ea0b3` except the later-added prefill test | PASS: 107 passed in 106 s | Local output. |
| Full Python suite (first run) | `python -m pytest tests -q -p no:cacheprovider` | working tree while the helper and tests were still being edited | 1726 passed, 2 failed, 5 skipped in 621 s | `test_snapshot_fingerprint_is_stable_across_python_hash_seeds` failed because a source edit landed between its subprocess hash reads; `test_isolated_direct_worker_entrypoint_from_another_directory` failed because `idna` was only in the user site, invisible to `python -I`. Both passed in isolation afterwards; the first is not reproducible on a stable tree and the second was an environment fix (`pip install --target` into the system site). |
| Full Python suite (second run) | `python -m pytest tests -q -p no:cacheprovider` | `b5ea0b3`, Linux | PASS: 1729 passed, 5 skipped, 1 warning in 618 s | Local output. The warning is the existing Starlette `BlockingPortal` deprecation. Skips are the pre-existing platform-conditional tests. |
| Frontend | `node --check app/static/flow_recording_editor.js`, `node --check app/static/flow_recordings.js`, `node --check app/static/flow_recording_model.js`, `node tests/test_recording_visual_model.mjs` | `b5ea0b3`, Node 22.22.2 | PASS | "Visual recording model tests passed". |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| FB-01, FB-02 live import | NOT RUN | Requires the work-PC recorder against the authenticated portal. | Record both journeys on the work PC and attach revision IDs. |
| FB-03 to FB-09 live trials | NOT RUN | Requires a signed-in GSCM session and private bookmark evidence. | Run the live matrix on the work PC after updating app and worker. |
| FB-10 old-worker claim and portable run | NOT RUN | No second worker or portal in the container; the bundle contents are covered synthetically. | Start an older worker and attempt a claim; run one portable Flow. |
| Native qualification | NOT RUN | The Luna investigation has no manual baseline plus two native trials. | Native selection stays disabled. |

## Findings, limitations and retests

Synthetic fixtures model a recycling, virtualized grid driven only by its
scrollbar buttons. They cannot prove Nexacro scrollbar timing, real row
recycling, folder-row visibility after scrolling, or that the recorded Go
opens the intended report. The 3-second observed-change wait and the
eight-press page size are inherited assumptions from the discovery sweep and
should be confirmed by FB-03/FB-04 timings in the run log's `bookmark` block.

## Merge evidence

CI for the implementation head `b5ea0b3` started on
[run 34092570375](https://github.com/datap0nd/data_governance/actions/runs/34092570375).
This report commit is the final head; its CI run URL, SHA and result are
recorded in the PR testing section before merge, and the PR merge record
supplies the merge SHA.
