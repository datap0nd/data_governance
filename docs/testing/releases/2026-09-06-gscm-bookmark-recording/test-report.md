# GSCM bookmark target recording: test report

- Change: scoped GSCM Favorite bookmark targets for recorded Flows.
- Tested revision: pending final PR head; this report is a pre-CI evidence cutoff and does not claim final-head CI.
- Environment: Windows, Python 3.13.15, local Chromium/Playwright synthetic fixtures.

| Coverage | Status | Result / evidence |
| --- | --- | --- |
| BK-01 editor repair and metadata validation | PENDING | Run affected browser and unit tests on final head. |
| BK-02/BK-04 deterministic resolution, visible/off-screen, duplicate, stalled cases | PENDING | Run `tests/test_recording_gscm_bookmark.py` on final head. |
| BK-06 ordinary clicks and portable execution | PENDING | Run affected regression tests and full Python suite on final head. |
| BK-03 live first/middle/last trials | NOT RUN | Requires authenticated work-PC GSCM and private bookmark evidence. |
| BK-05 live cancellation/wrong-frame/native partial effects | NOT RUN | Requires authenticated work-PC GSCM; synthetic coverage must not be treated as portal proof. |
| Native qualification | NOT RUN | The published Luna investigation has no completed baseline/two-native-attempt evidence. Native selection remains disabled. |
| Final CI | PENDING | Record final run URL and final SHA in the PR before merge. |

Warnings/skips and exact command output will be appended after final-head execution. No portal credentials, report data, private URLs, raw DOM traces, or downloads are included here.
