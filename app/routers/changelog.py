"""Changelog endpoint — returns version history from git log."""

import logging
import subprocess
from fastapi import APIRouter
from app.config import BASE_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/changelog", tags=["changelog"])


@router.get("")
def get_changelog():
    """Return version history built from git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H|%aI|%s", "--no-merges", "-50"],
            capture_output=True, text=True, timeout=10,
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            return []
    except Exception:
        return []

    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, date, msg = parts
        entries.append({
            "date": date,
            "title": msg,
            "description": "",
            "commit": sha[:7],
        })

    return entries
