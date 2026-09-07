# GSCM bookmark target recording: test report

- Change: scoped GSCM Favorite bookmark targets for recorded Flows.
- Tested revision: `f66480fef7f03ed0cb0134aef6f0f3fb51fe2038`; this report is a local evidence cutoff and does not claim final-head CI.
- Environment: Windows, Python 3.13.15, local Chromium/Playwright synthetic fixtures.

| Coverage | Status | Result / evidence |
| --- | --- | --- |
| BK-01 editor repair and metadata validation | PASS | `python -m pytest tests/test_recording_gscm_bookmark.py tests/test_flow_recordings.py::test_ui_repairs_a_recycled_gscm_row_as_an_exact_bookmark_target -q`: 5 passed in 2.23s. |
| BK-02/BK-04 deterministic resolution, visible/off-screen, duplicate, stalled cases | PASS | Included in the focused 5-test run above. |
| BK-06 ordinary clicks and portable execution | PASS | Full suite below includes recording click and portable execution regression coverage. |
| BK-03 live first/middle/last trials | NOT RUN | Requires authenticated work-PC GSCM and private bookmark evidence. |
| BK-05 live cancellation/wrong-frame/native partial effects | NOT RUN | Requires authenticated work-PC GSCM; synthetic coverage must not be treated as portal proof. |
| Native qualification | NOT RUN | The published Luna investigation has no completed baseline/two-native-attempt evidence. Native selection remains disabled. |
| Full local Python suite | PASS | `python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests','-q','--basetemp='+tempfile.gettempdir()+'/gscm-bookmark-full-'+uuid.uuid4().hex,'--junitxml=TEMP/gscm-bookmark-full-workaround.xml']))"`: 1,717 passed in 812.54s. The host workaround avoids pytest's disabled Windows symlink cleanup. |
| Final CI | PASS | PR head `3f4b5247d2deeeafa3ddebab33aa3c2fd456766a`: [Tests run 34058267082](https://github.com/datap0nd/data_governance/actions/runs/34058267082), pytest (ubuntu-latest) and pytest (windows-latest) both succeeded; merged as `28f3d563f3dbdd479cf31c3eda054ab4c3ff346e` in [PR #82](https://github.com/datap0nd/data_governance/pull/82). Recorded after merge by the 2026-09-07 fallback follow-up. |

Warnings: one existing Starlette/httpx TestClient deprecation and ten existing `timeout`-argument deprecations from `tests/test_flow_parallel.py`. No portal credentials, report data, private URLs, raw DOM traces, or downloads are included here.
