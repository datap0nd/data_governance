"""Vercel serverless entry point - exposes the FastAPI app as an ASGI handler."""

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so `from app.xxx` imports work
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Force DB to /tmp on Vercel (read-only filesystem elsewhere)
if "VERCEL" in os.environ or "VERCEL_ENV" in os.environ:
    os.environ.setdefault("DG_DB_PATH", "/tmp/governance.db")
    os.environ.setdefault("DG_SIMULATE_FRESHNESS", "true")
    os.environ.setdefault("DG_AI_MOCK", "true")

from app.main import app  # noqa: E402
