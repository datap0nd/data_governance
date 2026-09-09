# Range setting on one recorded step: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
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
