# Claude instructions for this repository

The policy for this repository lives in one file, imported here:

@AGENTS.md

Codex reads only `AGENTS.md`, so keep every rule that matters there. What
follows is true for Claude Code sessions specifically.

## Claude Code on the web

- There is no `gh` CLI. Use the GitHub MCP tools (`mcp__github__*`) to open the
  PR, read check runs, and merge.
- Merge with `mcp__github__merge_pull_request` pinned to the tested head SHA
  (`sha` parameter), so a late push cannot slip into the merge.
- The SessionStart hook in `.claude/settings.json` creates or refreshes the
  checkout-owned `.venv` with `python tools/check.py setup`. If it did not run,
  do it yourself before the first `verify`.
- The container is Linux without PowerShell: `python tools/check.py` is the
  verifier here, and its `result.json` is the local evidence to cite in the
  release report.
