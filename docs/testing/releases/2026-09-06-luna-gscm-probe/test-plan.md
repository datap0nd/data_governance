# Luna GSCM investigation handoff: test plan

- Change: documentation-only prompt for a 10-minute work-PC investigation.
- Baseline: `f74d64f977097d542ee3843ffc7b2cb52e6fa4b8`.
- Instructions: [Luna prompt](../../../luna_gscm_bookmark_probe.md).
- Results: [test-report.md](test-report.md).
- Environment: local Windows checkout for documentation review; the separate
  Luna experiment requires the signed-in work-PC browser and permitted DevTools.

## Prerequisites and cases

No portal access is needed to validate this documentation. Preserve the existing
checkout and inspect only the four changed Markdown files.

| ID | Actions | Expected result | Evidence |
| --- | --- | --- | --- |
| DOC-01 | Run `git diff --check`; validate relative links in the prompt, plan and report; check the new index links. | No whitespace errors; every new local target exists. | Command output and tested revision in report/PR. |
| DOC-02 | Read the prompt as a work-PC operator, walking each time segment and its failure exits. | Ten-minute limit; baseline; target initially unrendered; native selection and real Go distinguished; repeat/blocked branches; cleanup and report included. | Manual documentation review. |
| DOC-03 | Inspect `git diff --stat` and changed content for application edits or sensitive evidence. | Only handoff, plan/report and index change; no private portal URLs, credentials or report data. | Diff and changed-file list. |
| LIVE-01 | Luna follows the linked prompt on the owner's work PC. | Actual attempt table establishes success, failure, blocked or inconclusive status without overstating reliability. | Sanitized Luna report; NOT RUN until executed. |

## Repeatable documentation checks

Run `git diff origin/main HEAD --check`, then run this Python snippet from the
repository root (`python -` with a PowerShell here-string is sufficient):

```python
import pathlib, re, subprocess
root = pathlib.Path.cwd()
names = subprocess.check_output(['git', 'diff', '--name-only', 'origin/main', 'HEAD'], text=True).splitlines()
assert len(names) == 4 and all(n.startswith('docs/') and n.endswith('.md') for n in names)
count = 0
for name in names:
    path = root / name
    content = path.read_text(encoding='utf-8')
    if name == 'docs/testing/README.md':
        content = '\n'.join(line for line in content.splitlines() if '2026-09-06-luna-gscm-probe' in line)
        assert content
    for target in re.findall(r'\]\(([^)]+)\)', content):
        if '://' not in target:
            assert (path.parent / target.split('#')[0]).resolve().exists(), (name, target)
            count += 1
prompt = (root / 'docs/luna_gscm_bookmark_probe.md').read_text(encoding='utf-8')
for phrase in ['0:00-2:00', '2:00-4:00', '4:00-7:00', '7:00-9:00', '9:00-10:00', 'NOT RUN/BLOCKED', 'at most three opening', 'Do not wheel-scroll', 'not production reliability', 'Remove your breakpoints']:
    assert phrase in prompt, phrase
print(f'PASS: {count} local links; 10 required markers; 4 Markdown files')
```

## Acceptance and cleanup

DOC-01 through DOC-03 pass and final PR checks pass before merge. LIVE-01 is an
investigation delegated to the owner-side Luna session, not a prerequisite for
publishing its instructions. No changed application journey or preview applies.
No local app tests are required for this documentation-only change. CI still
runs the configured repository checks. No portal state is changed while writing
this handoff. Revert the documentation PR to withdraw the instructions.
