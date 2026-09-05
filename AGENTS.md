# Repository delivery instructions

The owner tests the deployed app from GitHub `main`. Complete implementation
requests through a tested PR merged to `main`; standing authorization covers
that merge. Use the session's branch naming rules and start from current
`origin/main`. Preserve unrelated work. Do not report a branch as deployed.

## Testing instructions and reports on every main merge

For **every PR merged to main**, including documentation, maintenance and fixes:

1. Create or update a release-specific **test plan and test report** under
   `docs/testing/releases/`. Follow [the testing workflow](docs/testing/README.md)
   and its templates. Add the package to the testing index.
2. Write concrete instructions for the changed behavior: prerequisites, UI
   actions or commands, expected results, negative/recovery cases, regression
   coverage, evidence to collect and cleanup. Scale the checks to the change;
   documentation-only changes need documentation checks, not invented app tests.
3. Run the affected tests; run the full Python suite for shared execution changes
   and frontend tests/syntax checks for frontend changes. Record actual commands,
   tested revision, environment, results, skips/warnings and evidence links.
4. Mark unperformed work-PC, portal, authentication or hardware checks **NOT RUN**
   or **BLOCKED**, with a reason. Synthetic tests and CI do not prove live success.
   Never prewrite a passing result for a test that has not finished.
5. Link the plan and report in the PR. Wait for required checks on the final head,
   record the final CI run and tested SHA in the PR's testing section, then merge.
   The PR preserves evidence produced after the committed report was written;
   a report must clearly identify that cutoff rather than imply later results.
6. Link the testing instructions and report in the delivery reply. State whether
   main was merged and which live checks remain. Later test results append dated,
   revision-specific evidence without erasing earlier outcomes.

Do not commit credentials, cookies, private portal URLs, raw report data or
unsanitized traces/logs. Reference protected evidence by an opaque identifier.
