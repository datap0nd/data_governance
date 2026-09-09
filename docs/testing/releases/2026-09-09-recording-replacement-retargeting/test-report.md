# Test report: recording replacement and template retargeting

## Summary

Cutoff: 2026-09-09 11:42 UTC. Local verification used an uncommitted worktree
based on `e6f2ca9262bb03f759592efdde040cd0d3175e1a`; final-head CI belongs in the
PR testing section. Environment: Windows ARM64, Python 3.13.15, Node 24.19.0,
Chrome 152.0.7977.83.

| Coverage | Result | Evidence |
| --- | --- | --- |
| Approved fictional journey | PASS | Owner approved the visible action row and Country→Main target editor in the Codex task on 2026-09-09. Local-only `retarget.html`; no portal data. |
| New semantic target and fallback cases | PASS | `2 passed in 4.38s`; `test_reports/recording-retarget-targets.xml` (local, uncommitted evidence). |
| Focused editor/template browser suites | PASS with runner warning | JUnit recorded 20 tests, 0 failures/errors/skips in 53.752s. The command then hit known Windows error 1463 while cleaning pytest convenience symlinks; superseded by the clean related run below. |
| Related recording regression | PASS | 94 passed, 1 Starlette deprecation warning in 245.77s, exit 0. `test_reports/recording-retarget-related-clean.xml` (local). |
| JavaScript model and syntax | PASS | Visual model test printed `Visual recording model tests passed`; `node --check` passed for app, recording model/editor, retarget preview and template preview scripts. |
| LIVE-01 | BLOCKED | Windows reports `UGREEN-25854` present/OK, but the local viewer remained black/stalled and no exact Flow identity was visible. No live portal action or signed run was attempted. |

## Commands and observations

Clean related regression used the repository-documented Windows workaround,
which disables only pytest's convenience symlink creation:

```powershell
python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests/test_flow_recordings.py','tests/test_recording_templates.py','tests/test_recording_journey.py','tests/test_recording_visual_editor.py','tests/test_optional_recording_editor.py','tests/test_recording_playback_ui.py','tests/test_recording_startup_ui.py','tests/test_templates_refresh_preview.py','-q','--basetemp='+tempfile.gettempdir()+'/recording-retarget-related-'+uuid.uuid4().hex,'--junitxml=test_reports/recording-retarget-related-clean.xml']))"
node tests/test_recording_visual_model.mjs
node --check app/static/app.js
node --check app/static/flow_recording_model.js
node --check app/static/flow_recording_editor.js
node --check app/static/recording-preview/retarget.js
node --check app/static/recording-preview/templates-refresh.js
git diff --check
```

The first broad attempt used `--basetemp` under the checkout and produced Flow
folder safety failures because test Flow roots must be outside the code checkout.
Those were environment setup failures, not accepted results; rerunning with an
external temporary root removed them. One actual compatibility assertion found
that the GSCM Favorite suggestion must remain first; the implementation was
corrected and the clean 94-case run includes that regression.

## Outstanding live evidence

LIVE-01 remains BLOCKED until the local capture feed shows the work PC and the
exact full Flow name/identity can be verified. Synthetic browser tests do not
claim portal, SSO, worker, download, or real-data success. When available,
append a dated revision-specific result without replacing this blocked entry.
