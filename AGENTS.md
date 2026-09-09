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
3. Run one smallest non-overlapping affected test set locally, plus applicable
   syntax checks. For application, dependency or test changes, required
   final-head CI is the authoritative full Python regression. Documentation,
   repository-policy, PR-template and workflow-only changes use the lightweight
   CI scope gate. Do not duplicate CI locally or run both a focused set and a
   superset containing the same cases unless diagnosing a failure. After a
   rebase, rerun application tests only when application/test code changed or a
   related conflict was resolved. Record actual commands, revision, environment,
   results, skips/warnings and evidence links.
4. Work-PC, live portal, authentication and hardware testing is opt-in only.
   Do not open, inspect, attempt, plan or report those checks unless the owner
   explicitly requests them in the current task. When they are not requested,
   omit them entirely rather than adding **NOT RUN** or **BLOCKED** placeholders.
   Synthetic fixture/browser tests remain allowed and must be identified as
   synthetic. Never invoke a Metronome/live-fix skill as part of testing unless
   the owner explicitly requests that skill in the current task. Never prewrite
   a passing result for a test that has not finished.
5. Link the plan and report in the PR. Wait for required checks on the final head,
   record the final CI run and tested SHA in the PR's testing section, then merge.
   The PR preserves evidence produced after the committed report was written;
   a report must clearly identify that cutoff rather than imply later results.
6. Link the testing instructions and report in the delivery reply and state
   whether main was merged. Mention only outstanding checks that were explicitly
   in scope. Later results append dated, revision-specific evidence without
   erasing earlier outcomes.

Do not commit credentials, cookies, private portal URLs, raw report data or
unsanitized traces/logs. Reference protected evidence by an opaque identifier.

## Usability review for changed journeys

Build a clickable local preview with fictional data using existing components,
fonts and design tokens. Obtain owner feedback before implementing a changed
journey. Approval applies to the demonstrated journey; material changes return
for review. Small wording or spacing fixes require usability review but no
separate approval pause.

Walk every changed control, including failure and recovery. Check clear labels,
visible feedback beside actions, preserved work, predictable navigation and a
clear next action. Record actual browser evidence tied to the tested revision.
Favor minimal screens and contextual questions over permanent configuration
forms; never hide a required choice under Advanced.
