"""Changelog endpoint — returns version history from git log."""

import logging
import subprocess
from fastapi import APIRouter
from app.config import BASE_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/changelog", tags=["changelog"])

# Curated feature descriptions keyed by commit hash prefix.
FEATURES = {
    # ── Apr 13 ──
    "6c92ac9": ("Scheduled Backup & Scan", "Daily automated backup at 6 AM and full scan at 7 AM"),
    "2f20f33": ("Simplified Health Status", "Removed at-risk tier, sources are now either healthy or degraded"),

    # ── Apr 10 ──
    "872924a": ("Pipeline Overview", "Interactive force-directed graph showing the full data pipeline at a glance"),
    "53d3085": ("Re-parse Button", "Re-run detection logic on existing scripts without a full directory walk"),

    # ── Apr 9 ──
    "d110237": ("Power Automate Flows", "Track Power Automate flows alongside scripts and scheduled tasks"),
    "9315ee3": ("Script Scanner Overhaul", "File and web source detection, variable resolution, reduced false positives"),

    # ── Apr 8 ──
    "2679b72": ("MV Dependency Scanning", "Automatic PostgreSQL materialized view dependency detection via pg_depend"),
    "891ed79": ("Multi-User Network Access", "IP-based identity so multiple team members can use the platform simultaneously"),
    "d1959db": ("Source Filter Buttons", "Quick filter tabs on Sources page: Excel/CSV, PostgreSQL, Has Script, Not Healthy"),
    "7452f5d": ("Task-Entity Linking", "Associate Kanban tasks with reports, sources, scripts, and other pipeline entities"),
    "c6a796a": ("MV Upstream in Lineage", "Materialized view upstream column added to lineage diagram"),
    "e897e39": ("pg_cron Schedule Scanning", "Detect pg_cron jobs and flag dependency freshness mismatches"),

    # ── Apr 6 ──
    "d80b0bb": ("Lineage Redesign", "Horizontal DAG layout with scripts and tasks columns replacing vertical tree"),
    "9567dc0": ("Multi-Machine Support", "Scripts and scheduled tasks tracked across multiple machines"),
    "bc9ba54": ("Direct PostgreSQL Probing", "Source freshness checked directly from PostgreSQL instead of CSV lookup"),
    "0da2bdc": ("PBI Refresh Sync", "Sync Power BI Service refresh schedules and detect overdue refreshes"),
    "1eef9c9": ("Archive Feature", "Archive stale reports, sources, scripts, and tasks to reduce noise"),

    # ── Apr 5 ──
    "491e9b0": ("Script Scanner", "Discover and track Python ETL scripts, detect SQL writes and file reads automatically"),
    "a8ccb29": ("Task Scheduler Integration", "Scan Windows Task Scheduler and link tasks to scripts for end-to-end tracking"),

    # ── Apr 3 ──
    "477a6af": ("Full Export", "Export all platform data as structured text under Tools menu"),

    # ── Apr 1 ──
    "bbe4b65": ("People Management", "Manage team members and assign owners to reports and sources from a central list"),
    "6668100": ("Version Display", "Commit hash shown in nav bar so you always know which version is running"),
    "8461935": ("Scanner Diagnostics", "Debug zero-report scans with detailed per-report parsing output"),

    # ── Mar 23 ──
    "963e729": ("Windows Service Installer", "One-click setup.ps1 installs the platform as a Windows service"),
    "1cdcfe1": ("UI Redesign", "Outfit font, teal accent, warm dark palette, bolder typography"),

    # ── Mar 16 ──
    "9699e5e": ("Accessibility & Dark Mode Audit", "ARIA attributes, keyboard navigation, contrast fixes, responsive improvements"),

    # ── Mar 10 ──
    "0a2bc01": ("Tasks Under Management", "Kanban board moved under Management nav group"),

    # ── Mar 9 ──
    "7115534": ("Kanban Task Manager", "Drag-and-drop task board with priorities, due dates, and assignees"),
    "1899135": ("Event Log", "Track all platform changes with user, entity, action, and detail columns"),
    "748732c": ("Renamed to MX Analytics", "Platform renamed from Data Governance Panel to MX Analytics"),
    "2dc3912": ("FAQ Page", "Frequently asked questions page under Admin dropdown"),
    "87131d6": ("TMDL Checker", "Best practices scanner renamed, with report owner filter"),

    # ── Mar 5 ──
    "25c0eea": ("Interactive Lineage Diagram", "Visual-to-source lineage with directed traversal and collapsible groups"),
    "5aac9c1": ("Best Practices Checker", "Automated report quality checks for local paths, missing owners, DirectQuery, and more"),
    "f4907bf": ("Unused Measures Detection", "Flag unused measures and columns in Power BI reports"),

    # ── Mar 4 ──
    "4959860": ("Create Page", "Manually add reports, sources, and upstream systems"),
    "08fe1ea": ("Owner Table Instructions", "Step-by-step guide for adding Report Owner and Business Owner tables to PBI reports"),

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
        "6c92ac9": "2026-04-13T13:16:23+00:00",
        "2f20f33": "2026-04-13T13:12:28+00:00",
        "872924a": "2026-04-10T12:58:24+00:00",
        "53d3085": "2026-04-10T13:09:08+00:00",
        "d110237": "2026-04-09T15:50:41+00:00",
        "9315ee3": "2026-04-09T18:07:18+00:00",
        "2679b72": "2026-04-08T10:05:09+00:00",
        "891ed79": "2026-04-08T13:17:07+00:00",
        "d1959db": "2026-04-08T17:54:59+00:00",
        "7452f5d": "2026-04-08T09:06:34+00:00",
        "c6a796a": "2026-04-08T14:12:35+00:00",
        "e897e39": "2026-04-08T12:48:30+00:00",
        "d80b0bb": "2026-04-06T15:43:42+00:00",
        "9567dc0": "2026-04-06T12:38:15+00:00",
        "bc9ba54": "2026-04-06T13:46:06+00:00",
        "0da2bdc": "2026-04-06T08:02:46+00:00",
        "1eef9c9": "2026-04-06T07:38:19+00:00",
        "491e9b0": "2026-04-05T22:11:21+00:00",
        "a8ccb29": "2026-04-05T23:24:04+00:00",
        "477a6af": "2026-04-03T19:19:02+00:00",
        "bbe4b65": "2026-04-01T16:31:55+00:00",
        "6668100": "2026-04-01T14:57:31+00:00",
        "8461935": "2026-04-01T14:29:38+00:00",
        "963e729": "2026-03-23T16:41:24+00:00",
        "1cdcfe1": "2026-03-23T15:57:40+00:00",
        "9699e5e": "2026-03-16T12:50:15+00:00",
        "0a2bc01": "2026-03-10T13:51:39+00:00",
        "7115534": "2026-03-09T09:06:01+00:00",
        "1899135": "2026-03-09T12:50:30+00:00",
        "748732c": "2026-03-09T12:26:51+00:00",
        "2dc3912": "2026-03-09T10:59:13+00:00",
        "87131d6": "2026-03-09T10:54:49+00:00",
        "25c0eea": "2026-03-05T12:25:21+00:00",
        "5aac9c1": "2026-03-05T11:08:41+00:00",
        "f4907bf": "2026-03-05T14:01:40+00:00",
        "4959860": "2026-03-04T05:26:45+00:00",
        "08fe1ea": "2026-03-04T07:30:25+00:00",
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
