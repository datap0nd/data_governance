# Claude Code notes

@AGENTS.md

The rules above apply to every agent. Notes specific to Claude Code:

- Web sessions run on Linux without PowerShell. The SessionStart hook in
  `.claude/hooks/session-start.sh` builds the checkout-owned `.venv` with
  `python tools/check.py setup`; use `python tools/check.py verify` for focused
  tests. Chromium is pre-installed and `PLAYWRIGHT_BROWSERS_PATH` is respected.
- There is no `gh` CLI in web sessions. Open the PR, read the `Merge ready`
  check and merge with the GitHub MCP tools, pinning the merge to the tested
  head SHA.
- Do not put model identifiers in commits, PR text or repository files.
