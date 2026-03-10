"""Changelog endpoint — returns version history from git log."""

import logging
import subprocess
from fastapi import APIRouter
from app.config import BASE_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/changelog", tags=["changelog"])

# Curated feature descriptions keyed by commit hash prefix.
FEATURES = {
    # ── Mar 3 ──
    "f40456a": ("Power BI Button Always Visible", "Open in Power BI button now always shows in report details, greyed out when no link is configured"),
    "a827af5": ("CSV Export", "Export button on Sources, Reports, and Issues pages to download filtered data as CSV"),
    "eca5812": ("Changelog Tab", "New Changelog page showing version history and feature releases"),
    "b5000e0": ("Power BI Report Links", "Click through from any report directly to its Power BI workspace via powerbi_links.csv"),
    "643c414": ("Live AI Assistant", "AI chat now connects to LiteLLM endpoint for real questions about your data ecosystem"),

    # ── Mar 2 ──
    "80fe3e0": ("Dashboard Visual Fix", "Removed colored backgrounds from status text on dashboard stat cards"),
    "47c0f43": ("Hide Unknown Sources", "Unknown and no-connection sources filtered from all views and no longer affect report health"),
    "0539a2e": ("Windows CSV Compatibility", "All CSV files read via pywin32 Excel COM on Windows for reliable encoding"),
    "2a4fde6": ("Scanner Shared Drive Fix", "Scans no longer try to access shared drive files; probe respects simulated freshness"),
    "75e9b85": ("Status Sort Order Fix", "Sources sort fresh-stale-outdated, reports sort current-at risk-degraded"),
    "4512cbc": ("CSV-Based Owner Assignment", "Report and business owners randomly assigned from owners.csv on each scan"),
    "03e2a80": ("Simplified Interface", "Removed AI briefing from dashboard, inline report expansion replaces bottom panel, Issues page is alerts-only"),
    "a8f80a5": ("Dashboard Redesign", "Clickable stat cards with navigation, health bar tooltips, unified Needs Attention list, pulse animation on critical items"),
    "b710ac7": ("AI Insights Engine", "AI-powered chat assistant, dashboard briefing, report risk assessment, and simulated source freshness probing"),

    # ── Feb 28 ──
    "034b9f6": ("4-Page Layout", "Consolidated UI into Dashboard, Sources, Reports, and Issues with dependency lineage view"),

    # ── Feb 27 ──
    "cef004f": ("Auto-Update Improvements", "Improved update.ps1 reliability with Chrome-based downloads and timeout handling"),
    "e257ff5": ("Actions & Alerts", "Action workflow system for managing stale/outdated sources, alert notifications, dark scrollbar theme"),
    "e533743": ("Professional Dark Theme", "Complete UI redesign with dark theme, Inter font, and polished card layouts"),
    "65d5b95": ("Sources Detail View", "Redesigned sources page with expandable detail panels and probe history"),
    "312a943": ("CSV-Based Freshness Probing", "Source freshness checked via latest_upload_date.csv instead of direct database connections"),
    "ff98732": ("PostgreSQL Source Probing", "Automatic last-updated timestamp checking for PostgreSQL data sources"),
    "1028268": ("Interactive Tables", "All data tables now have sortable columns and per-column text filters"),
    "bcbccda": ("Database Source Display", "Database sources show schema.table format instead of just server/database"),
    "8c45cf1": ("Extended Connector Support", "Added support for SQL Server, MySQL, Oracle, ODBC, OLEDB, SSAS, Redshift, Snowflake, BigQuery, SharePoint"),
    "d69d509": ("Direct PBIX Scanning", "Scan .pbix files directly using PBIXRay — no TMDL export step needed"),
    "7f2a995": ("Owner Metadata Extraction", "Business owner and report owner automatically extracted from TMDL metadata"),
    "a0122a8": ("Auto-Update Script", "PowerShell update.ps1 script for one-click updates from GitHub"),
    "5d9e74f": ("Windows Setup Guide", "Step-by-step installation instructions for Windows deployment"),
    "d48f0e6": ("Initial Release", "TMDL scanner, FastAPI REST API, web panel with source and report tracking"),
}


@router.get("")
def get_changelog():
    """Return version history built from git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H|%aI|%s", "--no-merges"],
            capture_output=True, text=True, timeout=10,
            cwd=str(BASE_DIR),
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
    """Fallback when git is unavailable — uses same FEATURES dict."""
    # Hardcoded dates for when git isn't available
    dates = {
        "f40456a": "2026-03-03T08:22:53+00:00",
        "a827af5": "2026-03-03T08:14:17+00:00",
        "eca5812": "2026-03-03T08:10:28+00:00",
        "b5000e0": "2026-03-03T08:04:55+00:00",
        "643c414": "2026-03-03T07:41:14+00:00",
        "80fe3e0": "2026-03-02T18:43:56+00:00",
        "47c0f43": "2026-03-02T18:30:54+00:00",
        "0539a2e": "2026-03-02T18:20:58+00:00",
        "2a4fde6": "2026-03-02T17:45:02+00:00",
        "75e9b85": "2026-03-02T17:27:35+00:00",
        "4512cbc": "2026-03-02T16:46:02+00:00",
        "03e2a80": "2026-03-02T16:40:12+00:00",
        "a8f80a5": "2026-03-02T16:19:54+00:00",
        "b710ac7": "2026-03-02T12:28:11+00:00",
        "034b9f6": "2026-02-28T20:04:00+00:00",
        "cef004f": "2026-02-27T13:06:19+00:00",
        "e257ff5": "2026-02-27T13:00:00+00:00",
        "e533743": "2026-02-27T12:23:10+00:00",
        "65d5b95": "2026-02-27T12:06:35+00:00",
        "312a943": "2026-02-27T11:31:49+00:00",
        "ff98732": "2026-02-27T09:32:37+00:00",
        "1028268": "2026-02-27T08:53:10+00:00",
        "bcbccda": "2026-02-27T08:06:25+00:00",
        "8c45cf1": "2026-02-27T08:02:03+00:00",
        "d69d509": "2026-02-27T07:52:55+00:00",
        "7f2a995": "2026-02-27T07:37:26+00:00",
        "a0122a8": "2026-02-27T07:14:04+00:00",
        "5d9e74f": "2026-02-27T07:01:34+00:00",
        "d48f0e6": "2026-02-27T06:47:39+00:00",
    }
    return [
        {"date": dates.get(k, ""), "title": v[0], "description": v[1], "commit": k}
        for k, v in FEATURES.items()
        if k in dates
    ]
