# Optional recording tests: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: recorded under Merge evidence.
- Evidence cutoff (UTC): 2026-09-11 17:37, after the implementation commit
  `a3ec639` and the follow-up test edit committed with this report.
- Tested code revision: the working tree of `claude/loving-edison-m6tt28`
  on top of `4c81929` (`a3ec639` plus the `tests/test_flow_recordings.py`
  edit below); the PR testing section records the final tested SHA.
- Environment: Linux container, Python 3.13.12 in a checkout-owned virtual
  environment built from `requirements-ci.lock` (`python3.13 -m venv .venv`,
  `pip install -r requirements-ci.lock`; `tools/check.ps1` needs PowerShell,
  which the container lacks), Playwright 1.62.0 with the preinstalled
  Chromium 141 aliased to the Chrome channel path, Node 22.22.2. No Edge,
  Windows, Flow worker or portal is available here.
- Overall finding: automated checks PASS; the Edge parameter of one
  unchanged browser test is a container limitation (CI installs Chrome and
  Edge); live BI-desktop confirmation (OT-01, OT-03) is an owner check and
  was NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| OT-01, OT-02, OT-05…OT-10 (focused) | `python -m pytest tests/test_recording_journey.py tests/test_recording_templates.py tests/test_flow_recordings.py tests/test_flow_portable_script.py tests/test_flow_handover.py tests/test_flow_standalone.py -q` | `a3ec639` working tree, Linux venv, isolated `DG_DB_PATH`/`DG_TEST_RUN_ROOT`/`DG_BROWSER_PROFILE_ROOT` | 89 passed, 2 failed, 2 warnings in 196.50 s. Failure 1: `test_flow_recordings.py::test_revision_validation_and_activation_freezes_configuration` still expected the retired 409 after a filename change (`DID NOT RAISE HTTPException`). Failure 2: `test_same_portable_pipeline_runs_real_download_on_both_browsers[msedge]`, `Chromium distribution 'msedge' is not found at /opt/microsoft/msedge/msedge`; the `[chrome]` parameter passed. | Console; the journey test drove the shipped recording editor in Chromium (`test_browser_real_api_save_test_return_apply` passed). |
| OT-01 retest | `python -m pytest "tests/test_flow_recordings.py::test_revision_validation_and_activation_records_evidence_without_gating" -q` | same tree after renaming that case and asserting the new behaviour (`_build_job` succeeds after the filename change with the same revision, `tested` False and the new template applied; `tested` True before) | PASS: 1 passed in 9.21 s | Console. No application code changed for the retest. |
| Edge parameter re-check | `python -m pytest "tests/test_flow_recordings.py::test_same_portable_pipeline_runs_real_download_on_both_browsers" -q` | same | `[chrome]` PASS; `[msedge]` FAIL with the same `not found at /opt/microsoft/msedge/msedge` launch error (no Edge in the container). The test is unchanged by this PR; CI installs both browsers. | Console. |
| OT-03, OT-04 (builder contract) | `node tests/test_flow_save_without_test.mjs`; `node tests/test_flow_builder_contract.mjs` | same | PASS (`save without a recording test confirmation tests passed`; `flow builder payload and error tests passed`; `flow builder email step tests passed`) | Console: no `confirm(` in the save handler, `recording_revision_id` sent, `allow_untested_recording` absent from `app.js`, unsaved-draft stop preserved. |
| Syntax | `python -m py_compile app/flow_recordings.py app/routers/flows.py app/routers/flow_recordings.py app/flow_portable.py`; `for f in app/static/*.js; do node --check "$f"; done` | same | PASS | Console. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| OT-01, OT-03 live | NOT RUN | No Metronome app, worker or portal in the container; the API and builder behaviour is covered synthetically. | On the BI desktop after updating from `main`: change one setting on a tested recorded Flow and save; save one never-tested new recording; confirm neither shows a dialog or a 409 and both run. |

## Findings, limitations and retests

- The one real failure was the pre-existing assertion that a settings change
  after a test must raise `validate` from `_build_job`; that is exactly the
  gate this release removes, so the case now asserts the run proceeds with
  the current settings and records `tested` False. Retest passed.
- Existing `approved` (saved without test) revisions keep their status and
  remain runnable; no migration runs. The retired `allow_untested_recording`
  request field is ignored by the API model (extra fields are not rejected).
- The `recording.tested` job key is new; older worker code ignores unknown
  keys, and `run_flow.py` regenerates on the next save or startup with the
  new header lines.
- Warnings are the two known `httpx`/`anyio` deprecation notices from the
  test client, unrelated to this change.

## Merge evidence

Final-head CI is pending at this cutoff. Record its run URL and exact head
SHA in the PR before merging; the PR's merge record supplies the merge SHA.
