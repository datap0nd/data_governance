# Testing documentation process: test plan

This change adds the standing main-merge testing requirement, reusable templates,
an index and the first cumulative Flows instructions/report/worksheet. It does
not change application, worker, installer or CI execution code.

- Source baseline: `bfff7bd0aa3af4a21f02706db65bc3b3e0524694`.
- Change: [PR #70](https://github.com/datap0nd/data_governance/pull/70).
- Results: [test-report.md](test-report.md).

| ID | Actions | Expected result |
| --- | --- | --- |
| DOC-01 | Open root README → testing index → Flows plan/report/worksheet. Open testing links from recorded_flows.md and flow_workers.md. Resolve all relative Markdown file targets in the changed docs. | All local targets exist; the main index, feature docs and plan/report provide working navigation. No local workstation paths in GitHub instructions. |
| DOC-02 | Compare case IDs in the Flows plan to rows in manual-results.csv. Read the CSV with a standard parser; verify all initial statuses. | Exactly one initial row per case, unique IDs, all NOT RUN. Columns support timestamp, tester, revisions, browser, session/run, actual result, evidence and defect tracking. |
| DOC-03 | Compare the Flows report's PR/head/main SHAs and CI counts to gh pr view 69, gh run view 33974422732 and its logs. Compare the retained local full-suite summary. | Exact evidence attribution; correct pass/skip/warning counts. Live portal/hardware claims remain pending. Historical fixed failures are not presented as current failures. |
| DOC-04 | Read AGENTS.md, CLAUDE.md, the PR template, testing index and both templates together. | Every main merge requires a plan/report, proportional tests, final-head CI evidence in PR, links in delivery, and explicit unperformed live checks. No recursive requirement to commit a report's own future SHA; no unsupported claim of an automated documentation gate. |
| DOC-05 | Review git diff --check and changed paths; review feature instructions against docs/recorded_flows.md, docs/flow_workers.md and tests. | Clean whitespace, documentation-only scope, correct UI labels/range examples. No credentials, internal URLs, workbooks or traces committed. Failure injection uses isolated resources. |
| DOC-06 | After pushing, open the PR's files and rendered GitHub docs; wait for final CI, record run/head/result in PR. After merge verify GitHub main contains the package. | GitHub exposes the plan/report/CSV. Final CI result is attributed to this PR, separately from historical #69 evidence. Main includes the testing package. |

Use Python standard-library `pathlib`, `re` and `csv` to check file targets and
case coverage; use `git diff --check` for whitespace. Existing CI runs the full
application/frontend suite on this documentation PR as configured. No new
application tests are needed for this documentation-only change.

Acceptance: DOC-01–DOC-05 pass before push; DOC-06 completes before the delivery
reply. No app update or work-PC access is needed just to read these documents.
Cleanup consists of retaining only intended documentation in the commit.
