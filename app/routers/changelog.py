"""Changelog endpoint — returns version history from git log."""

import logging
import subprocess
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/changelog", tags=["changelog"])

# Curated feature descriptions keyed by commit hash prefix.
# Commits not listed here are grouped under their date as minor updates.
FEATURES = {
    "b5000e0": ("Power BI Links", "Reports now link directly to Power BI workspace via powerbi_links.csv"),
    "643c414": ("Live AI Chat", "AI chat connects to LiteLLM endpoint for real questions about your data ecosystem"),
    "47c0f43": ("Filter Unknown Sources", "Unknown/no-connection sources hidden from UI and no longer affect report health status"),
    "2a4fde6": ("Scanner Fix", "Scans no longer attempt to access shared drive files; respects simulated freshness setting"),
    "0539a2e": ("Windows CSV Support", "CSV files read via pywin32 Excel COM on Windows for reliable encoding handling"),
    "03e2a80": ("Simplified UI", "Removed AI briefing from dashboard, inline report expansion, alerts-only Issues page"),
    "a8f80a5": ("Dashboard Redesign", "Clickable stat cards, health bar tooltips, unified Needs Attention list, pulse animation on critical items"),
    "4512cbc": ("Owner Assignment", "Report and business owners randomly assigned from owners.csv on each scan"),
    "b710ac7": ("AI Insights & Freshness", "AI-powered chat, briefing, risk assessment, and simulated source freshness probing"),
    "034b9f6": ("4-Page Consolidation", "Merged pages into Dashboard, Sources, Reports, and Issues with dependency view"),
    "e257ff5": ("Actions & Alerts System", "Workflow actions, alert management, dark theme polish"),
    "e533743": ("UI Redesign", "Professional BI monitoring dashboard with dark theme"),
    "65d5b95": ("Sources Page Redesign", "Redesigned sources view with detail panels"),
    "312a943": ("CSV-Based Probing", "Source freshness probing via latest_upload_date.csv instead of direct DB connection"),
    "ff98732": ("PostgreSQL Probing", "Source freshness checking for PostgreSQL data sources"),
    "1028268": ("Sortable Tables", "All tables now have sortable and filterable columns"),
    "d69d509": ("PBIX Scanning", "Direct .pbix file scanning via PBIXRay — no TMDL export needed"),
    "7f2a995": ("Owner Extraction", "Business owner and report owner extracted from TMDL metadata"),
    "d48f0e6": ("Phase 1 Launch", "TMDL scanner, REST API, and web panel — initial release"),
}


@router.get("")
def get_changelog():
    """Return version history built from git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H|%aI|%s", "--no-merges"],
            capture_output=True, text=True, timeout=10,
            cwd="/workspace/data_governance",
        )
        if result.returncode != 0:
            return _static_changelog()
    except Exception:
        return _static_changelog()

    entries = []
    seen_features = set()
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, date, msg = parts
        short = sha[:7]

        if short in FEATURES and short not in seen_features:
            title, description = FEATURES[short]
            entries.append({
                "date": date,
                "title": title,
                "description": description,
                "commit": short,
            })
            seen_features.add(short)

    return entries


def _static_changelog():
    """Fallback when git is unavailable."""
    return [
        {"date": "2026-03-03T08:04:55+00:00", "title": "Power BI Links", "description": "Reports now link directly to Power BI workspace", "commit": "b5000e0"},
        {"date": "2026-03-03T07:41:14+00:00", "title": "Live AI Chat", "description": "AI chat connects to real LLM endpoint", "commit": "643c414"},
        {"date": "2026-03-02T18:30:54+00:00", "title": "Filter Unknown Sources", "description": "Unknown sources hidden and don't affect report status", "commit": "47c0f43"},
        {"date": "2026-03-02T16:40:12+00:00", "title": "Simplified UI", "description": "Inline report expansion, alerts-only Issues page", "commit": "03e2a80"},
        {"date": "2026-03-02T16:19:54+00:00", "title": "Dashboard Redesign", "description": "Clickable stat cards, health bar tooltips, unified attention list", "commit": "a8f80a5"},
        {"date": "2026-02-27T06:47:39+00:00", "title": "Phase 1 Launch", "description": "TMDL scanner, REST API, and web panel", "commit": "d48f0e6"},
    ]
