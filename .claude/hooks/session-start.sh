#!/bin/bash
# Prepare the checkout-owned Python 3.13 .venv for Claude Code on the web so
# focused tests can run immediately. Local sessions are left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

expected="$(sha256sum requirements-ci.lock | cut -d' ' -f1)"
installed=""
if [ -f .venv/.metronome-ci-lock.sha256 ]; then
  installed="$(tr -d '[:space:]' < .venv/.metronome-ci-lock.sha256)"
fi

if [ "$installed" != "$expected" ] || [ ! -x .venv/bin/python ]; then
  python3.13 tools/check.py setup
fi

python3.13 tools/check.py preflight
