# Optional recording checks: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-06, before the implementation commit.
- Revision: working changes based on
  `995ebb21289b6af8086e5c9c2a0de6156835e18f`. The PR testing section records
  the final SHA and CI produced after this committed report's cutoff.
- Environment: Windows ARM64, Python 3.13.15, Node 24.19.0,
  Playwright 1.62.0, Chrome 152.0.7977.76. All browser fixtures use fictional
  data; no portal credentials or business data are included.

## Executed checks

| Check | Result |
| --- | --- |
| Import/test/save/reopen journey | 4 passed, one Starlette TestClient deprecation warning in 13.29s |
| Existing visual editor browser suite | 13 passed |
| New optional editor controls | Initial run: 2 passed, 1 failed on mobile toolbar overflow; fixed by wrapping controls. Retest: 3 passed in 8.52s. Added HTML/text format-switch and legacy-check recovery: 6 passed in 13.88s. The added tests initially timed out on an exact wrapping-label selector; corrected selector, no product defect. |
| Backend recording/model/browser regressions | Initial combined run: 68 passed, 3 failed due to test filename settings. Corrected test fixtures to use the same declared download format. Combined retest: 71 passed in 71.74s. A later focused run passed 23 browser/schema cases and failed 3 portable cases because an overlapping earlier run held the same global Flow lock; stopped the overlap, with serial verification below. |
| Recorded storage and portable pipeline | Initial run: 9 passed, 1 failed because the sign-in fixture did not use a recognized existing marker. Corrected fixture to the shared `sign in to continue` marker; storage retest: 8 passed in 4.24s. Both automated/portable transformation cases passed in the initial run. |
| Node suites | All 22 passed after the final HTML/text UI correction |
| Changed JavaScript syntax and `git diff --check` | PASS; ordinary Git LF-to-CRLF checkout notices only |
| Full Python suite | 1614 passed, 2 failed, 10 Starlette deprecation warnings in 604.47s. Both failures were the Chrome/Edge portable tests refusing the global Flow lock held by the overlapping focused run; no assertion failed after replay began. Serial recovery results below. |
| Serial recovery | Both previously blocked Chrome/Edge cases plus all 26 optional-recording cases passed: 28 passed in 236.20s, no warnings. Evidence: OS temporary directory `metronome-optional-recording-serial.xml`. No production changes were needed. |
| Final fictional shell fixture | All 26 optional-recording browser/schema/portable tests passed in 40.19s, no warnings, with two aligned navigation buttons. |
| Final-head Windows/Linux CI | PENDING at report cutoff; final run, exact head SHA and results must be recorded in the PR before merge |

The local Python invocation uses `pytest.main` with `-q --tb=short`, an
external `tempfile.mkdtemp(prefix='metronome-...-')` for `--basetemp`, and
`_pytest.pathlib._force_symlink=lambda *a,**k:None` for Windows temporary
directories. CI uses ordinary `python -m pytest tests -q`. The full local
run writes JUnit evidence to the OS temporary directory as
`metronome-optional-recording-full.xml`. After collecting the full suite, the
fictional portal fixture gained a navigation row to avoid unnecessary
portal-shell waits. One button was insufficient; two aligned buttons form the
expected fictional shell. Production code is unchanged. Final fixture
verification and final CI use those two buttons.

## Behavior and browser evidence

The user explicitly requested this correction to the previously approved
journey. No additional confirmation was imposed. The
[clickable preview](../../../../app/static/recording-preview/optional.html)
uses production editor/model components and mocked fictional APIs.

- [Recording without questions](evidence/recording.png)
- [Optional 60-second wait](evidence/wait.png)
- [Optional row check failure](evidence/optional-check.png)
- [Mobile controls](evidence/mobile.png)

Screenshots were inspected at desktop and 390px width. Direct testing,
wait insertion/movement, save/reopen, failure, check removal, undo and
navigation are exercised by real browser controls. Real backend fixtures
cover empty valid reports, ignoring legacy generated metadata, explicit
assertions, optional thresholds and preserving previous published output
when a later download fails its check.

Unchecked recordings preserve valid downloaded bytes. CSV/Excel normalization
is performed when optional data checks, trimming, transforms or SQL require
it; other source types retain their existing import requirements. Native
download completion, recognized sign-in responses and workbook integrity
still matter. Row checks exclude the header and blank rows. Old generated
identity/readiness metadata is ignored, while explicitly recorded assertion
steps and user-configured data checks remain active and editable.

Independent review identified HTML/text check creation, blank-inclusive
row-count evidence, portable-path coverage and generic indexed-locator gates;
these corrections are included before final validation.

## Unperformed checks

LIVE-1, actual work-PC worker launch, portal sign-in, GSCM/ASAP download timing,
real 60-second waits, UNC share permissions and downstream Power BI refresh:
**NOT RUN**. This host serves the fictional preview and has no live app/worker
runtime for the owner's work PC. Follow the plan after updating both app and
worker; synthetic and CI tests do not prove live portal success.
