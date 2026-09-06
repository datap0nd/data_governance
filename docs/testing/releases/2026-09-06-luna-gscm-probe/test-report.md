# Luna GSCM investigation handoff: test report

- Plan: [test-plan.md](test-plan.md).
- Change: documentation-only [Luna prompt](../../../luna_gscm_bookmark_probe.md),
  [PR #81](https://github.com/datap0nd/data_governance/pull/81).
- Evidence cutoff: 2026-09-06 19:00 UTC; local documentation checks only.
- Tested revision: `8f377378f749580c67f31e6cbaf06ed079c4d317`, plus the
  uncommitted repeatable-check snippet added to the plan. This report was updated
  after those checks; final revision and final checks belong to the PR record.
- Environment: Windows / PowerShell, local isolated Git worktree.
- Overall finding: documentation checks PASS; work-PC investigation NOT RUN.

## Executed checks

| IDs | Actual command / procedure | Result | Evidence |
| --- | --- | --- | --- |
| DOC-01 | `git diff origin/main HEAD --check`, then `git diff origin/main --check` including the plan snippet | PASS after correction; no remaining whitespace errors. | Local command output. |
| DOC-01/02/03 | Execute the Python block under the plan's Repeatable documentation checks using `python -` | PASS: 7 local links, 10 timebox/guard/report markers, exactly 4 Markdown files. No skips. | Local command output; script is reproduced in the plan. |
| DOC-02 | Manual operator walkthrough of all five time segments, including missing target/auth/tool capability, failed manual baseline, unknown handlers, auto-rendering, identity mismatch, and timeout | PASS: bounded attempts, explicit inconclusive/blocked reporting, cleanup, and no claim that selection equals opening. | Prompt reviewed at the revision above. |
| DOC-03 | `git diff HEAD^ HEAD --stat` on the initial handoff commit; subsequent patch inspection | PASS: only documentation/index changes; no business evidence or credentials included. | Git diff and PR file list. |

The first `git diff HEAD^ HEAD --check` on
`7ebb8b306831c4c04ce359e97e105c555766cb22` reported a blank line at the end of
the prompt. It was removed in `8f377378`; the check then passed. Git emitted
ordinary LF-to-CRLF working-copy notices. No application tests were run locally
because no executable code or application journey changed.

## Unperformed checks

| ID | Status | Reason / next action |
| --- | --- | --- |
| LIVE-01 | NOT RUN | Luna on the owner's work PC must run the prompt using the actual authenticated portal. |

## Limits and merge evidence

This release supplies investigation instructions, not a GSCM fix or evidence of
successful native activation. Final-head CI and exact tested SHA will be recorded
in the PR after this report's commit and before merge. No application behavior,
recording schema or browser-control implementation changes.
