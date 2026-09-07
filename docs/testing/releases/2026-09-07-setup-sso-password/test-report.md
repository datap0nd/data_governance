# Setup shared SSO password: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-07, before final PR CI; final results are recorded in
  the PR testing section after this committed report.
- Tested code: `87bf76d902f5ea334bcd25ced7c850f20774b2b7` based on `e0353e0d`.
- Final focused tests: `f691bf3fe6bede652afd9aff3f85a13c99427306` adds independent
  encryption/replacement failure coverage; production code is unchanged.
- Change: [implementation commit](https://github.com/datap0nd/data_governance/commit/87bf76d902f5ea334bcd25ced7c850f20774b2b7).
- Environment: local Windows ARM64, Python 3.13.15, Windows PowerShell.
- Overall: focused automated checks pass; full suite pending; live SSO NOT RUN.

## Executed checks

| Check | Command/procedure | Revision/environment | Result | Evidence |
| --- | --- | --- | --- | --- |
| SSO-01 through SSO-06 and updater regression | `python -m pytest tests/test_setup_sso_password.py tests/test_flow_credentials.py tests/test_unattended_update_scripts.py -q -p no:cacheprovider --basetemp=.test-setup-sso-focused --junitxml=.test-setup-sso-focused.xml` | Uncommitted implementation subsequently committed unchanged as `87bf76d9`; local Windows | 18 test cases passed, but command exited 1 at session cleanup: WinError 1463 following pytest's convenience symlink. Not counted as a clean command pass. | Local `.test-setup-sso-focused.xml`, 18 tests, 0 failures/errors/skips, 3.566 s. |
| Focused rerun | `python -X utf8 -c "import _pytest.pathlib as p,pytest,tempfile,uuid;p._force_symlink=lambda *a,**k:None;raise SystemExit(pytest.main(['tests/test_setup_sso_password.py','tests/test_flow_credentials.py','tests/test_unattended_update_scripts.py','-q','--basetemp='+tempfile.gettempdir()+'/metronome-sso-focused-'+uuid.uuid4().hex,'--junitxml=.test-setup-sso-verified.xml']))"` | `87bf76d9`; local Windows | PASS: 18 passed in 2.26 s, exit 0, no warnings/skips. | Local `.test-setup-sso-verified.xml`; aggregates retained here. |
| Setup syntax | PowerShell Parser invocation in the plan | `87bf76d9`; local Windows | PASS, no parse errors. | Console result `PowerShell parse PASS`. |
| Final focused rerun | Same focused workaround command above, with `--junitxml=.test-setup-sso-final-focused.xml` | `f691bf3f`; local Windows | PASS: 19 passed in 2.28 s, exit 0, no warnings/skips; separate encryption and file-replacement failure tests. | Local `.test-setup-sso-final-focused.xml`; aggregates retained here. |
| Full Python suite | Exact full-suite workaround command in the plan | Started at `87bf76d9`, all test modules collected before the test-only follow-up `f691bf3f` | RUNNING at committed-report cutoff; no passing outcome claimed. | Final local result and final-head CI will be recorded in the PR testing section. |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| LIVE-01 through LIVE-04 | NOT RUN | No authorized real work-PC execution in this implementation run. The earlier capture-device check found no UGREEN-25854 feed. Local DPAPI tests use fictional data. | Owner runs the plan after installing merged main and records revision-specific sanitized outcomes. |

## Findings, limitations and retests

The focused rerun disables only pytest's optional temporary-directory symlink
creation, as documented by prior releases. It does not alter Windows policy,
application path checks, encryption, or authentication behavior. Actual Windows
DPAPI encryption/decryption and the setup PowerShell subprocess boundary passed
against isolated fictional credentials. Portable unit tests stub DPAPI only to
exercise failure and no-op cases. No real portal login or Windows service change
was performed. No frontend changes; browser preview is not applicable to this
existing setup/environment-variable behavior.

## Merge evidence

Final PR CI and merge are pending at this report's cutoff. The PR testing section
will record its final tested head SHA, CI run URLs/results and merge record before
delivery. These later results do not imply work-PC deployment or live SSO success.
