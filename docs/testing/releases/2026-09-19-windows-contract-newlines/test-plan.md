# Cross-platform Flow builder contract: test plan

- Change/PR: make the Flow builder source-contract test independent of Git checkout newline conversion after the merged `main` Windows regression exposed an LF-only assumption.
- Code baseline: `864d4eba2b3948837a153d72a01a12e91ba6b683`; the PR records its final tested head.
- Related report: [test-report.md](test-report.md)
- Intended environments: isolated Windows/Node/Python verification and final-head CI.

## Prerequisites and test data

Use the checkout-owned Python 3.13 environment created by `tools/check.ps1`.
The contract reads repository JavaScript only and uses no application database,
browser profile, credentials, exports, or external services.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| N-01 | Run `tests/test_flow_builder_contract.mjs` through its focused pytest wrapper from a Windows checkout where `app/static/app.js` has CRLF line endings. | The source is normalized before source-shape assertions; the Python arguments and values accessibility assertions both pass. | Focused verifier result. |
| N-02 | Syntax-check the changed Node contract file. | Node accepts the script with no syntax error. | Focused verifier result. |
| N-03 | Review the change against the failed `main` job and the neighboring contract assertions. | Only test input normalization changes; application behavior and UI remain untouched; LF and CRLF checkouts exercise identical assertions. | Diff review and failed-run link. |
| R-01 | Run required final-head CI on the PR. | `Merge ready` passes for the exact recorded head SHA. | PR testing section and CI run. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flow_excel_ui.py::test_flow_builder_frontend_contract `
  -SyntaxPath tests/test_flow_builder_contract.mjs
```

Run one bounded diff review covering newline normalization scope and both
multi-line source assertions. Do not repeat the full local suite; final-head CI
owns the full regression.

## Acceptance and cleanup

Accept when the focused verifier passes on Windows, the changed Node file is
syntax-clean, the diff review finds no application change, and required
final-head CI passes. Verifier scratch data is isolated and removed according
to `tools/check.ps1`; rollback is a test-only code rollback.
