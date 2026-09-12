#!/bin/bash
# Prepare the checkout-owned Python 3.13 environment so the first
# `python tools/check.py verify` in a web session can run immediately.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$PWD}"

marker=".venv/.metronome-ci-lock.sha256"
expected="$(sha256sum requirements-ci.lock | cut -d' ' -f1)"
if [ -x .venv/bin/python ] && [ -f "$marker" ] &&
   [ "$(tr -d '[:space:]' < "$marker")" = "$expected" ]; then
  echo "Metronome: .venv already matches requirements-ci.lock."
  exit 0
fi

echo "Metronome: creating or refreshing the checkout-owned .venv."
python3 tools/check.py setup
