# Range setting on one recorded step: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: PR #96 and follow-up template-copy fix (pending PR)
- Evidence cutoff (UTC): 2026-09-09 17:40; final CI is recorded in the PR
- Tested revision: uncommitted changes based on `85fd02482442787002e802e37a21e6432151455d`
- Environment: Windows ARM64, Python 3.13, Node 24, synthetic Chrome through Playwright
- Overall: PASS for the approved journey and focused behavior; final-head CI is pending

## Results

- UI-01--03: PASS. The owner approved the clickable fictional journey with
  **Advanced -> This is a range step** on each step and only one recorded week click.
- UI-01--03, REG-01, and FAIL-01: PASS. The final isolated focused run of
  `tests/test_recording_range_editor.py` completed 1 test in 2.70 seconds. Local
  JUnit evidence: `test_reports/range-advanced-focused-final2.xml`. The same
  browser test verified the disabled setting and explanation on a step without
  an element target.
- MODEL-01: PASS. JavaScript syntax checks and model assertions completed without errors.

## Follow-up evidence: copied v3 range recordings

- Evidence cutoff (UTC): 2026-09-09 20:22; final-head CI is recorded in the follow-up PR.
- Tested revision: uncommitted fix based on merge `2be72c299497d80d727a17c12991894db966b4d9`.
- COPY-01: PASS. The isolated synthetic Chrome test
  `tests/test_templates_refresh_preview.py::test_copying_range_template_preserves_recording_version_three`
  copied a v3 range template, edited and saved it, then validated the submitted
  definition as version 3. Result: 1 passed in 4.74 seconds; local JUnit evidence
  is `test_reports/range-template-v3.xml`.
