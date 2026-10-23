# 2026-10-23: Python Flows write their own email body: test plan

- Change/PR: link in the PR's testing section. A Python-script Flow with the
  Email step on gives its last script row `METRONOME_FLOW_EMAIL_DIR` (only
  workers advertising `python_script_email_dir_v1` claim such a job); when the
  script writes `body.html` and its `cid:` images there, the Email step sends
  that body with the images inline and the final file attached, plus one
  Metronome footer line. A body that breaks a rule falls back to the standard
  message, and the run's email detail says why. Wizard's recurring briefs use
  it.
- Code baseline: `e48263a` (`main` before this change).
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container, checkout-owned Python 3.13 `.venv`,
  synthetic fixtures only; Outlook hand-offs are captured, never launched.

## Prerequisites and test data

Synthetic scripts and files under pytest's `tmp_path`; the Outlook dispatch is
captured by the existing `outlook` fixture in `tests/test_flow_email_delivery.py`.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| E-01 | Two-row Python job with the Email step on; the last script writes `body.html` and `chart.png` into `METRONOME_FLOW_EMAIL_DIR` | Only the last row sees the variable; the files are in `<run folder>/email` | `test_email_step_gives_only_the_last_script_row_an_email_folder` |
| E-02 | Same job, the script writes no body | No `email` folder exists: the worker names it and never creates or deletes it | same test |
| E-03 | Same scripts, Email step off | Neither row sees the variable; no folder; run-only steps drop the variable | same test |
| E-04 | Succeeded Python run with a registered run folder holding `body.html` and two referenced images plus one unreferenced image | Outlook message body is the script's HTML with the Metronome footer before `</body>`; only the two referenced images are inline, each once, with their `cid`; the final file is attached; event says the script wrote the body | `test_python_script_body_is_sent_with_inline_images_and_the_file` |
| E-05 | Same, but the body references a missing image | Standard "Flow file" message with the file; email detail and event explain the fallback | `test_unusable_script_body_falls_back_to_the_standard_message` |
| E-06 | `script_body` with a portal source, no run folder, `cid:../secret.png`, `cid:chart.svg`, an empty body, a symlinked image, non-UTF-8 bytes, an oversized body, oversized images and too many images | `None` for the first two; a `ScriptBodyError` naming the rule for each of the others | `test_script_body_rules` |
| E-07 | `_with_footer` with and without `</body>` | Footer before the closing tag (any case), else appended | `test_script_footer_goes_before_the_closing_body_tag` |
| E-08 | Queued runs of a Python Flow with the Email step on and one without; a worker without `python_script_email_dir_v1`, then one with it | The older worker takes only the Flow without email; the current worker takes the emailed one; run-only jobs never need the capability | `test_worker_claim_requires_the_email_dir_capability_only_when_the_email_step_is_on` |
| R-01 | Existing email delivery, Python worker, portable and standalone script tests, and the worker-never-deletes invariants (`test_worker_source_contains_no_delete_or_overwrite_operation`, `test_retention_module_is_the_only_deletion_site_and_gates_every_path`) | Unchanged behavior | Affected suites below |

## Automated checks

```bash
python tools/check.py verify --test tests/test_flow_email_delivery.py --test tests/test_flow_python.py \
  --syntax app/flow_email_delivery.py --syntax app/flow_python.py --syntax app/flow_worker.py
python tools/check.py verify --test tests/test_flow_email_delivery.py --test tests/test_flow_python.py \
  --test tests/test_flow_desktop_session.py \
  --test tests/test_flows.py::test_worker_source_contains_no_delete_or_overwrite_operation \
  --test tests/test_flows.py::test_retention_module_is_the_only_deletion_site_and_gates_every_path \
  --syntax app/flow_email_delivery.py --syntax app/flow_python.py --syntax app/flow_worker.py \
  --syntax app/routers/flows.py
python tools/check.py verify --test tests/test_flow_run_mode.py --test tests/test_flow_portable_script.py \
  --test tests/test_flow_handover.py --test tests/test_flow_standalone.py
```

Final-head CI (`Merge ready`) is the full regression.

## Acceptance and cleanup

Accepted when E-01 to E-07 and R-01 pass locally and final-head CI passes.
Nothing to clean up: fixtures live in pytest temporary folders. Rollback:
revert the PR; Flows then send the standard message again, and a script that
still looks for `METRONOME_FLOW_EMAIL_DIR` finds the variable unset.
