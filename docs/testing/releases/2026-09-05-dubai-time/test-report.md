# Dubai time: test report

Plan: [test-plan.md](test-plan.md). Source baseline:
`69cf793fb0bff0480b79c28ebd8ab5d291137b14`; evidence below concerns the implementation
working tree on 2026-09-05. The delivery PR records the final tested commit and CI.

| Check | Result at document cutoff |
| --- | --- |
| Five time-policy tests: clock preservation, midnight, historical instant migration, idempotence, frozen JSON, replacement draft and browser-independent display | PASS: 5 passed, 1.17 s |
| All 21 frontend test files | PASS |
| Python compileall and three frontend syntax checks | PASS |
| Initial affected Python run | 276 passed, 4 failed: assertions expected the old local-clock storage contract. Expectations/UTC lease fixture updated to the new contract; final full run pending. |
| Full Python regression run and final Windows/Linux CI | Pending at document cutoff; final outcome must be recorded in the delivery PR before merge. |
| TIME-01 through TIME-05 on work PC | NOT RUN: requires updated work-PC app/workers and live portal/recipient test resources. |

Local commands used `pytest.main` with an external temporary directory and the
previously documented Windows ARM pytest symlink-cleanup workaround. CI uses
normal `python -m pytest tests -q`. No live portal success is claimed.
