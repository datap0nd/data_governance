# Flow folder names and save-time Python refresh: test report

- Change/PR: [PR #126](https://github.com/datap0nd/data_governance/pull/126).

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-15 06:22 UTC. Final PR CI has not run at this cutoff.
- Baseline: `b39c8193`; initial implementation: `b15028b4`; corrected implementation: `3b605ac916f6bf68ae3997f34d7dd950f7b8b501`.
- Environment: Windows, Python 3.13.15 in the repository's existing virtual environment; Codex in-app browser for fictional preview.

## Initial review and baseline

Code review confirmed that `flow_folder_slug` appended the ID and Save updated only
the manifest name. Post-commit generation already runs synchronously. Its snapshot
omitted transformation file contents, allowing an in-place edit to evade refresh.

The initial global Python test collection failed because Windows Application Control
rejected its greenlet DLL. The existing repository virtual environment imports the
dependency successfully. Its baseline run reached 23 passing test cases but exited
with an error when pytest attempted to resolve its optional `current` symlink aliases
(WinError 1463). This is not recorded as a successful test invocation. Subsequent
local runners suppress only those convenience aliases, preserving application link
checks and all tests.

## Synthetic browser evidence

The preview uses production editor components, fonts and tokens with fictional
in-memory responses. It does not demonstrate a real filesystem rename.

- U-01: entering Weekly orders and Save showed `metronome/flows/GSCM/Weekly orders/`, Python revision 2 and `Flow saved`.
- U-02: collision and unavailable-folder scenarios showed inline `Flow not saved` errors, retained Monthly orders in the form and left the saved tree unchanged. Normal-save retry changed the tree to Monthly orders.
- U-03: Python-refresh failure showed the newly saved folder with the previous Python name/revision and an explicit files-could-not-update status. Edit flow → Normal save → Save refreshed Python and showed Flow saved.
- Owner response: **Approve this behavior**, received in this task before production implementation.

The final preview files were included in `b15028b4`. Initial browser observations
were made against the same working files; a small fixture correction made the
Python failure display preserve its old name and cleared prior status on Edit.
U-03 was exercised again after that correction.

## Implementation checks

| Check | Revision | Actual result | Evidence |
| --- | --- | --- | --- |
| Folder/layout/handover focused set (47 cases) | `b15028b4` | 45 passed, 2 failed; 91.78 s; no skips | Local synthetic JUnit ID `flow-name-focused-1` |
| Rename and refresh regression after corrections (27 cases) | `3b605ac9` | 27 passed; 67.63 s; no skips or warnings | Local synthetic JUnit ID `flow-name-focused-2` |
| Python syntax: 7 changed modules/tests via ast.parse | `b15028b4` | PASS | Local command output |
| Corrected rename module/test syntax via ast.parse | `3b605ac9` | PASS | Local command output |
| `node --check app/static/recording-preview/folder-names.js` | `3b605ac9` | PASS | Local command output |
| `git diff --check` and `git diff --check origin/main...HEAD` | `3b605ac9` plus release docs | PASS (Git emitted ordinary LF/CRLF conversion notices) | Local command output |

The two initial failures were (1) a Windows history folder key lost its normalized
casing, fixed by recomputing the key using the existing canonical helper; and
(2) an assertion expected catalog Python to embed transformation source. Catalog
scripts execute their saved transformation path, while recorded scripts embed the
source. The revised test verifies the current content hash/path for catalog and
the embedded source for recorded execution. Both cases passed in the retest.

The retest includes the changed rename module plus added case-only interruption
and pending-publication cases. The 23 existing layout/handover cases passed in the
first implementation run and were not duplicated locally after the corrections.
Final-head CI remains the authority for the full regression.

Actual local test invocation used the existing interpreter
`C:/Users/keeoh/Documents/ChatGPT/Metronome/.venv/Scripts/python.exe`, with the
following script piped to it in PowerShell. The first invocation used all three
files; the second selected only `tests/test_flow_folder_rename.py` and changed
both evidence suffixes to `2`:

```python
import pytest, _pytest.pathlib
_pytest.pathlib._force_symlink = lambda *args, **kwargs: None
raise SystemExit(pytest.main([
    'tests/test_flow_folder_rename.py', 'tests/test_flow_layout.py',
    'tests/test_flow_handover.py', '-q',
    '--basetemp=C:/Users/keeoh/Documents/ChatGPT/flow-name-focused-1',
    '--junitxml=C:/Users/keeoh/Documents/ChatGPT/flow-name-focused-1.xml',
]))
```

The generated-script test runs the saved file with `sys.executable -I`, a fresh
fixture lock root and synthetic CSV input, and verifies exit 0 / `succeeded`.
Recording tests inspect the newly selected generated definition without executing
it. Browser review also included a visual layout check after the corrected code
commit. No production UI controls were added; the scenario selector is preview-only.

## 2026-09-15 06:33 UTC: legacy adoption follow-up

Review found that an unmanaged legacy Flow could keep attempting allocation at its
old name after the user supplied a new name. Save now allocates using the pending
name, allowing recovery from an old-name collision without touching that folder.

At `61741f47`, the new collision case plus the existing legacy-save/history and
adoption-idempotence companions passed: **3 passed in 12.94 s**, no skips/warnings.
Both changed Python files passed `ast.parse`. The same runner wrapper above used
these selectors and evidence ID `flow-name-legacy-final`:

```text
tests/test_flow_folder_rename.py::test_unmanaged_legacy_save_allocates_new_name_despite_old_name_collision
tests/test_managed_flow_editor.py::test_saving_legacy_flow_manages_future_output_and_preserves_history
tests/test_flow_layout.py::test_adoption_keeps_historic_target_and_is_idempotent
```

Only the affected legacy companions were repeated for this correction. Earlier
results retain their original revisions. The prior PR CI was still running on
`7bdac239`; it is superseded by the final push and is not final-head evidence.
Final-head CI results after this appended cutoff belong in the PR testing section.

## 2026-09-15 06:51 UTC: full CI findings and focused recovery

[CI run 34937508854](https://github.com/datap0nd/data_governance/actions/runs/34937508854)
on head `6904e1932fdfec61e2c56c2f099756b145b191c6` completed with **5 failed,
2,142 passed, 26 skipped and 14 warnings** across the two Python shards. Shard 0
passed 1,074 cases in 769.19 s; shard 1 passed 1,068 and failed five in 635.76 s.
Frontend and Windows verifier checks passed (Windows: 17 passed in 5.39 s).
The merge gate correctly failed. No merge was attempted.

Four failures were missing settings-database fixture isolation in scanner and
view-refresh tests. The Flow fixture and scanner helper now point both
`database.DB_PATH` and `settings.DB_PATH` at their synthetic database. The fifth
was a recording test assuming its tested destination stayed unchanged on rename.
It now verifies that evidence is historical after the folder move, then performs
a synthetic validation at the saved destination before asserting current evidence.
No production recording gate was introduced: Save still works without testing.

At `07483af1`, all five previously failing cases passed locally in **56.54 s**,
with no skips/warnings. The three changed test files passed `ast.parse`. The same
local wrapper selected the five nodes listed below, using temporary/evidence
suffix `ci-recovery` (JUnit ID `flow-name-ci-recovery`):

```text
tests/test_modular_scanner.py::test_notification_recipients_normalize_and_disable
tests/test_modular_scanner.py::test_stalled_notification_is_queued_once_and_reconciled
tests/test_recording_journey.py::test_pending_snapshot_atomic_apply_and_active_evidence
tests/test_view_refresh.py::test_pipeline_plan_includes_flow_views_once_and_child_runs_defer
tests/test_view_refresh.py::test_active_flow_view_refresh_blocks_pipeline_preview
```

Scanner dispatches, recording completion and database refresh operations in these
checks are synthetic substitutes. The original failure evidence remains above.
The full regression on the next final head is pending at this appended cutoff.

## Merge evidence

Final CI is pending. Before merge the PR testing section will record the final head,
required checks, run URL and results produced after this report's committed cutoff.
The PR merge record supplies the actual main merge SHA.
