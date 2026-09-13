# Flow topic groups: test report

- Latest delivery state: implementation ready; focused local checks complete; final-head CI pending.
- Latest evidence cutoff: 2026-09-13 11:16:08 UTC (test runs completed by 11:13:42; subsequent image inspection included).
- Change and final merge evidence: [PR #122](https://github.com/datap0nd/data_governance/pull/122).
- Implementation revision: `8c93eebb81f438008f1d75a406947740fef23cad`.
- Scope: persistent group organization and atomic group queueing, with the owner-approved UI.

## Initial preview evidence

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-13 10:49:52 UTC.
- Base: `84a8ea9` (`origin/main`, PR #121).
- Preview revisions: `3593be02778beef423e1a2a5d536c0010e0cba52` and `55882c21a9523b467fe9db735d312950a0f2da15`.
- Environment: Windows 10.0.26200; PowerShell 7.6.5; checkout-owned Python 3.13.15; Playwright 1.62.0; Chrome 152.0.7977.83. Desktop 1440×1080 and narrow 390×844.
- Finding at the initial preview cutoff: all three synthetic browser cases passed across the initial run and focused retest after a menu-dismissal fix. Implementation was awaiting owner feedback at that time.

## Executed checks

| Check | Actual command/procedure | Revision | Result | Evidence |
| --- | --- | --- | --- | --- |
| Environment | `.\tools\check.ps1 -Mode Setup` | `84a8ea9` | PASS; created this checkout's Python 3.13 environment and installed locked dependencies. | Run `20260913T104035950Z-39956-e6efdad8`. |
| G-01–G-06, syntax | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_topic_groups_preview.py -SyntaxPath app/static/recording-preview/groups.js,tests/test_flow_topic_groups_preview.py` | `3593be02778beef423e1a2a5d536c0010e0cba52` | 2 passed, 1 failed, 0 skips. Pytest 38.97 s; wrapper 42.913 s. JS/Python syntax passed. | Run `20260913T104836972Z-7860-a2cef39e`; [original browser metadata](evidence/browser.json). |
| G-03/G-04 retest | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_topic_groups_preview.py::test_create_edit_move_ungroup_and_save_failure -SyntaxPath app/static/recording-preview/groups.js,tests/test_flow_topic_groups_preview.py` | `55882c21a9523b467fe9db735d312950a0f2da15` | PASS, 1 case, 0 skips; pytest 3.28 s, wrapper 5.365 s. JS/Python syntax passed. | Run `20260913T104945988Z-44580-6a0c08e0`; [retest browser metadata](evidence/browser-55882c2.json). |

Both Verify commands set `PREVIEW_EVIDENCE_DIR` exactly as shown in the plan.
Machine-readable raw results and JUnit are in `.test-runs/<run-id>/` in this
checkout; sanitized command/result summaries are in [local-preview-checks.json](local-preview-checks.json).
No test warnings were reported. Git emitted its normal LF-to-CRLF notice while
staging new files; this was not a test failure.

## Usability evidence

| Evidence | Revision and observation |
| --- | --- |
| [Collapsed groups](evidence/groups-collapsed.png) | `3593be0`: group and standalone names align within 2 px; source headings remain. |
| [Expanded group](evidence/groups-expanded.png) | `3593be0`: both members retain separate SQL destinations, Run and Edit controls. |
| [Running group](evidence/group-running.png) | `3593be0`: two fictional member runs appear in the existing execution pane; duplicate group action disabled. |
| [Save recovery](evidence/group-save-recovery.png) | `55882c2`: name and checkbox selection retained after simulated save failure; successful retry observed. |
| [Blocked group run](evidence/group-run-blocked.png) | `3593be0`: member-specific error beside Run group; no member queued; retry succeeds with visible capacity waiting. |
| [Narrow search](evidence/group-search-narrow.png) | `3593be0`: two selections retained while searching 197 ASAP flows out of 200 total at 390×844. |

The cases also cover Cancel, close ×, rename, duplicate-name validation, moving
membership, ungrouping without losing flows, source isolation and return to the
list. Completed cases reported no page JavaScript errors or external requests.

Owner feedback was requested on 2026-09-13 using the clickable local preview.
The owner then approved: “Approve this design and continue to implementation
and merge.” The production implementation follows that demonstrated journey.

## Findings and retests

The initial G-03/G-04 case timed out when reopening **More > Edit group** after
Cancel. The More menu had stayed open behind the dialog, so a second More click
closed it. The preview now dismisses the menu when entering the group editor.
Only the failed case was rerun; it passed. No test assertion was removed or
weakened. The two unaffected initial passes remain attributed to their original
revision; they are not claimed as reruns on the corrected head.

The initial preview results are synthetic interaction evidence. Its runs and
organizational changes exist solely in tab memory. Existing view/editor buttons
outside grouping explain their unchanged destination in a preview dialog.
The separate implementation evidence below exercises actual persistence and
queueing.

## Implementation evidence: 2026-09-13

The implementation set contains 22 pytest cases, including two browser journeys
using real group HTTP endpoints and an isolated database. The Node bridge runs
the three existing source-group, sort and polling contracts. Each case has a
passing result in the runs below; earlier passes retain their original revision.
The browser companion was repeated only after the status code and browser
selector changed. No local full regression or blanket reuse was claimed.

| Revision | Actual selection and syntax | Result | Evidence |
| --- | --- | --- | --- |
| `c8a5f6fcee70b091c00b32acb3a05df9c3ce90d6` | Full focused command in the plan: `tests/test_flow_topic_groups.py`, `tests/test_flow_topic_groups_browser.py`, update-drain test; nine listed syntax paths. | 4 passed, 18 fixture setup errors, 2 dependency warnings; pytest 29.82 s, wrapper 33.299 s. Syntax passed. | Run `20260913T110553273Z-40880-1207e0e9`. |
| `99bb570d2a60e78e919923371f487c9dcdc3a377` | Only the 18 errored selectors from the prior JUnit; syntax `tests/test_flow_topic_groups.py`. | 17 passed, 1 browser selector failure, 0 skips; 2 dependency warnings; pytest 317.89 s, wrapper 320.024 s. | Run `20260913T110659339Z-36624-310ae630`. |
| `8c93eebb81f438008f1d75a406947740fef23cad` | `tests/test_flow_topic_groups_browser.py`; syntax `app/static/flow_groups.js,tests/test_flow_topic_groups_browser.py`. | 2 passed, 0 skips; 2 dependency warnings; pytest 44.06 s, wrapper 46.261 s. | Run `20260913T111254894Z-33028-2d315711`; [browser metadata](evidence/implementation-browser.json). |

Exact selections, environment, source fingerprints, UTC times, result counts
and evidence paths are retained in [local-implementation-checks.json](local-implementation-checks.json).
The commands used `GROUP_EVIDENCE_DIR` as shown in the plan. The 18-case retry
selection was derived without rerunning successful cases:

```powershell
[xml]$priorJUnit = Get-Content -Raw .test-runs/20260913T110553273Z-40880-1207e0e9/pytest.xml
$failedSelectors = @($priorJUnit.testsuites.testsuite.testcase | Where-Object { $_.error -or $_.failure } | ForEach-Object { $_.classname.Replace('.', '/') + '.py::' + $_.name })
.\tools\check.ps1 -Mode Verify -TestPath $failedSelectors -SyntaxPath tests/test_flow_topic_groups.py
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_topic_groups_browser.py -SyntaxPath app/static/flow_groups.js,tests/test_flow_topic_groups_browser.py
```

### Implementation findings and recovery

1. The PowerShell verifier supplies one isolated managed-folder root for the
   run. The first fixture recreated the same five names and IDs in separate
   databases while reusing that root, causing exclusive folder creation errors.
   Each group fixture now uses a distinct child of the verifier-owned root.
   The 16 affected backend cases and actual worker-claim checks then passed.
2. A browser checkbox lookup matched both the modal choice and underlying Flow
   row controls. The test now scopes that lookup to the group dialog; it keeps
   the same required action/assertions. The actual CRUD/reload journey passed.
3. Bounded diff review found that old batch feedback could reappear during a
   later individual run. Completed batch feedback is now cleared and identical
   polling messages are not reannounced. The browser retest asserts **1 of 2
   active** after running one member alone.

The locked Starlette dependencies emit deprecations for the `httpx` TestClient
integration and `anyio.abc.BlockingPortal`. Synthetic setup also logged
`FileNotFoundError` warnings from the existing derived continuity-file writer
under the isolated Windows paths. These did not invalidate the group database,
queueing or UI assertions; derived continuity-file generation was not changed
by this release. No warnings or tests were suppressed.

### Actual implementation browser evidence

All three images below were captured at `8c93eebb81f438008f1d75a406947740fef23cad`
and visually inspected. The synthetic fixture includes real temporary file paths
and fictional report metadata; worker launch is replaced with a fixture.

- [Persistent groups after reload](evidence/implementation-groups.png): moved member, group count, aligned standalone rows and individual actions.
- [Narrow group editor](evidence/implementation-editor-narrow.png): preserved name/selection after duplicate-name rejection, all required controls visible at 390×844.
- [Actual queued group](evidence/implementation-queued.png): two durable queued members and the existing execution pane.

The browser walks also verified failed-save recovery, ungroup/reload, individual
Run/Stop and preserving an unsaved group name/selection while activity polling
returns completed runs to their group. Both final browser cases reported no
JavaScript page errors.

## Outstanding in-scope work and merge evidence

CI's initial scope gate at head `283a41440424f7cd8118bbb5fdd4f3b33fdc06cb`
rejected one extra blank line at the end of `app/static/flow_groups.css`:
[run 34753995930](https://github.com/datap0nd/data_governance/actions/runs/34753995930).
The subsequent documentation head carried the same whitespace. The extra line
was removed on 2026-09-13 after this finding. This changes no styling or
application behavior, so the focused application cases were not repeated.

Final-head CI is pending at the committed report cutoff. Before merging, the PR
testing section must record the exact tested head SHA and passing **Merge ready**
run URL. Those later results belong in the PR so the report's own commit does
not keep moving the tested head. The PR merge record supplies the actual merge
SHA. This report does not claim deployment verification.

After the cutoff, automated PR review identified the missing entry in the
complete release index. The release is now listed in both indexes, and the
recent-releases table retains its five-entry limit. The two plan/report targets
were inspected during this documentation correction. Application code and
tests did not change; no application tests were repeated locally for this fix.
The corrected head's CI result will be recorded in the PR as described above.

## Additional CI diagnosis and targeted recovery: 2026-09-13

The superseded [CI run 34754053742](https://github.com/datap0nd/data_governance/actions/runs/34754053742)
at `6939d376c066ab8fe0e6346cb7bd605e76a03b89` was cancelled after the index
correction. Its partial Python log contained three failures and three teardown
errors before cancellation, without a completed test summary. Inspection found
that the existing worksheet UI's fictional HTTP server rejected the newly
required `/api/flows/groups` read. It now returns an empty group list; its
assertions and worksheet behavior were not changed.

At `5ae61c5c065097fa1162e3a89de0bd2eb36238cf`, the exact I-11 command in the
plan passed all three affected browser cases, with zero skips or warnings.
Python syntax passed. Pytest took 9.13 s; the verifier took 11.047 s, finishing
at 2026-09-13 11:31:25 UTC with the same checkout-owned Windows/Python/browser
environment. Evidence: run `20260913T113114336Z-34784-f7cbbda1`,
[sanitized result](local-worksheet-checks.json). This extends the original 22
implementation passes with three existing worksheet regression cases. Only
these affected cases were run locally; final-head CI remains pending until
its result is recorded in the PR.
