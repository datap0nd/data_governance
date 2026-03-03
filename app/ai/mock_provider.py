"""Smart mock AI provider that generates contextual responses from real database data."""

import re
from datetime import datetime, timezone


def _short_name(full_name: str) -> str:
    """Extract short display name from a source path."""
    if not full_name:
        return ""
    normalized = full_name.replace("\\", "/")
    last_slash = normalized.rfind("/")
    base = normalized[last_slash + 1:] if last_slash >= 0 else normalized
    # For DB-style names like "dbo.Orders", keep as-is
    if "." in base and not re.search(r"\.(csv|xlsx|xls|json|parquet|txt)$", base, re.I):
        return base
    dot = base.rfind(".")
    return base[:dot] if dot > 0 else base


def _time_ago(date_str: str) -> str:
    if not date_str:
        return "never"
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        diff = now - d
        mins = int(diff.total_seconds() / 60)
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        days = hrs // 24
        return f"{days}d ago"
    except Exception:
        return date_str


# ── Chat mock ──

def mock_chat(message: str, context: dict) -> dict:
    """Generate a contextual chat response based on keyword matching and real data."""
    msg_lower = message.lower().strip()
    sources = context.get("sources", [])
    reports = context.get("reports", [])
    alerts = context.get("alerts", [])
    actions = context.get("actions", [])
    edges = context.get("edges", [])

    # Categorize sources by status
    fresh = [s for s in sources if s.get("probe_status") == "fresh"]
    stale = [s for s in sources if s.get("probe_status") == "stale"]
    outdated = [s for s in sources if s.get("probe_status") == "outdated"]
    no_conn = [s for s in sources if s.get("probe_status") == "no_connection"]
    unknown = [s for s in sources if s.get("probe_status") in (None, "unknown")]
    # Display labels
    healthy, at_risk, degraded = fresh, stale, outdated

    # Build report-source map
    report_sources = {}
    for e in edges:
        rid = e.get("report_id")
        if rid not in report_sources:
            report_sources[rid] = []
        report_sources[rid].append(e)

    sources_referenced = []
    reports_referenced = []

    # ── "risk" / "at risk" / "what's wrong" ──
    if any(kw in msg_lower for kw in ["risk", "at risk", "wrong", "problem", "issue", "concern"]):
        lines = ["## Risk Summary\n"]
        if no_conn:
            names = ", ".join(f"**{_short_name(s['name'])}**" for s in no_conn[:8])
            lines.append(f"**{len(no_conn)} SQL sources have NO CONNECTION status:** {names}")
            # Find affected reports
            no_conn_ids = {s["id"] for s in no_conn}
            affected = set()
            for e in edges:
                if e["source_id"] in no_conn_ids:
                    affected.add(e.get("report_id"))
            if affected:
                affected_names = []
                for r in reports:
                    if r["id"] in affected:
                        affected_names.append(r["name"])
                        reports_referenced.append(r["id"])
                lines.append(f"\nThese feed into **{len(affected)} reports**: {', '.join(affected_names[:6])}")
            sources_referenced = [s["id"] for s in no_conn]
            lines.append("")
        if at_risk:
            names = ", ".join(f"**{_short_name(s['name'])}**" for s in at_risk[:5])
            lines.append(f"**{len(at_risk)} at-risk sources:** {names}")
        if degraded:
            names = ", ".join(f"**{_short_name(s['name'])}**" for s in degraded[:5])
            lines.append(f"**{len(degraded)} degraded sources:** {names}")
        if not no_conn and not at_risk and not degraded:
            lines.append("No significant risks detected. All monitored sources are healthy.")

        open_actions = [a for a in actions if a.get("status") == "open"]
        if open_actions:
            lines.append(f"\n**{len(open_actions)} open action items** require attention.")

        return {"response": "\n".join(lines), "sources_referenced": sources_referenced, "reports_referenced": reports_referenced}

    # ── "summarize" / "dashboard" / "overview" / "status" ──
    if any(kw in msg_lower for kw in ["summarize", "dashboard", "overview", "status", "how are", "health"]):
        scan = context.get("last_scan")
        scan_ago = _time_ago(scan.get("started_at")) if scan else "never"
        lines = ["## Ecosystem Overview\n"]
        lines.append(f"Tracking **{len(sources)} data sources** across **{len(reports)} Power BI reports**.\n")
        lines.append("| Status | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| Healthy | {len(healthy)} |")
        lines.append(f"| At Risk | {len(at_risk)} |")
        lines.append(f"| Degraded | {len(degraded)} |")
        lines.append(f"| No Connection | {len(no_conn)} |")
        lines.append(f"| Unknown | {len(unknown)} |")
        lines.append(f"\n**{len(alerts)} active alerts**, **{len([a for a in actions if a.get('status') == 'open'])} open actions**.")
        lines.append(f"Last scan: **{scan_ago}**.")
        if len(fresh) == len(sources) and len(sources) > 0:
            lines.append("\nAll sources are fresh and healthy.")
        elif no_conn:
            lines.append(f"\n**Note:** {len(no_conn)} SQL sources have no connection — these should be investigated.")
        return {"response": "\n".join(lines), "sources_referenced": [], "reports_referenced": []}

    # ── "at risk" / "stale" ──
    if "at risk" in msg_lower or "stale" in msg_lower:
        if at_risk:
            lines = [f"## {len(at_risk)} At-Risk Sources\n"]
            lines.append("These sources have data that is 31-90 days old:\n")
            for s in at_risk:
                lines.append(f"- **{_short_name(s['name'])}** ({s.get('type', '?')}) — last updated {_time_ago(s.get('last_updated'))}")
                sources_referenced.append(s["id"])
            return {"response": "\n".join(lines), "sources_referenced": sources_referenced, "reports_referenced": []}
        return {"response": "No at-risk sources detected. All monitored sources are either healthy, degraded, or have no connection.", "sources_referenced": [], "reports_referenced": []}

    # ── asking about a specific report ──
    for r in reports:
        rname_lower = r["name"].lower()
        short = _short_name(r["name"]).lower()
        if short in msg_lower or rname_lower in msg_lower:
            lines = [f"## Report: {r['name']}\n"]
            lines.append(f"- **Owner:** {r.get('owner') or 'not set'}")
            lines.append(f"- **Business Owner:** {r.get('business_owner') or 'not set'}")
            lines.append(f"- **Frequency:** {r.get('frequency') or 'not set'}")
            lines.append(f"- **Sources:** {r.get('source_count', 0)}")
            # Find this report's sources
            rsrcs = report_sources.get(r["id"], [])
            if rsrcs:
                lines.append("\n**Data Sources:**")
                for e in rsrcs:
                    src = next((s for s in sources if s["id"] == e["source_id"]), None)
                    if src:
                        st = src.get("probe_status") or "unknown"
                        lines.append(f"- {_short_name(src['name'])} ({src.get('type', '?')}) — **{st}**")
                        sources_referenced.append(src["id"])
            reports_referenced.append(r["id"])
            return {"response": "\n".join(lines), "sources_referenced": sources_referenced, "reports_referenced": reports_referenced}

    # ── asking about a specific source ──
    for s in sources:
        sname_lower = _short_name(s["name"]).lower()
        if sname_lower and sname_lower in msg_lower:
            lines = [f"## Source: {_short_name(s['name'])}\n"]
            lines.append(f"- **Type:** {s.get('type', '?')}")
            lines.append(f"- **Status:** {s.get('probe_status') or 'unknown'}")
            lines.append(f"- **Last Updated:** {_time_ago(s.get('last_updated'))}")
            lines.append(f"- **Connection:** {s.get('connection_info') or 'n/a'}")
            # Find which reports use this source
            using = [e for e in edges if e["source_id"] == s["id"]]
            if using:
                rnames = []
                for e in using:
                    rpt = next((r for r in reports if r["id"] == e["report_id"]), None)
                    if rpt:
                        rnames.append(rpt["name"])
                        reports_referenced.append(rpt["id"])
                lines.append(f"\n**Used by {len(using)} reports:** {', '.join(rnames)}")
            sources_referenced.append(s["id"])
            return {"response": "\n".join(lines), "sources_referenced": sources_referenced, "reports_referenced": reports_referenced}

    # ── "audit" ──
    if any(kw in msg_lower for kw in ["audit", "query", "m expression", "power query"]):
        from app.ai.query_auditor import mock_audit_all
        from app.ai.context_builder import get_report_context
        from app.database import get_db
        with get_db() as db:
            all_reports = [dict(r) for r in db.execute("SELECT * FROM reports").fetchall()]
        all_data = []
        for r in all_reports:
            ctx = get_report_context(r["id"])
            if ctx:
                all_data.append(ctx)
        result = mock_audit_all(all_data)
        return {"response": result["response"], "sources_referenced": [], "reports_referenced": []}

    # ── "no connection" / "sql" ──
    if any(kw in msg_lower for kw in ["no connection", "no_connection", "sql connection", "connection"]):
        if no_conn:
            lines = [f"## {len(no_conn)} Sources With No Connection\n"]
            for s in no_conn[:10]:
                lines.append(f"- **{_short_name(s['name'])}** ({s.get('type', '?')})")
                sources_referenced.append(s["id"])
            lines.append("\nThese SQL sources could not be probed. This typically means the SQL Server is not reachable from this environment.")
            return {"response": "\n".join(lines), "sources_referenced": sources_referenced, "reports_referenced": []}
        return {"response": "All sources have active connections.", "sources_referenced": [], "reports_referenced": []}

    # ── "alert" ──
    if "alert" in msg_lower:
        if alerts:
            lines = [f"## {len(alerts)} Active Alerts\n"]
            for a in alerts[:8]:
                lines.append(f"- **{a.get('severity', '?')}**: {a.get('message', 'No message')}")
            return {"response": "\n".join(lines), "sources_referenced": [], "reports_referenced": []}
        return {"response": "No active alerts. Your data ecosystem is alert-free.", "sources_referenced": [], "reports_referenced": []}

    # ── "action" ──
    if "action" in msg_lower:
        open_actions = [a for a in actions if a.get("status") == "open"]
        if open_actions:
            lines = [f"## {len(open_actions)} Open Actions\n"]
            for a in open_actions[:8]:
                name = _short_name(a.get("source_name") or a.get("report_name") or "")
                lines.append(f"- **{name}** — {a.get('type', '?')} ({a.get('status')})")
            return {"response": "\n".join(lines), "sources_referenced": [], "reports_referenced": []}
        return {"response": "No open actions. All issues have been addressed.", "sources_referenced": [], "reports_referenced": []}

    # ── generic fallback ──
    lines = [
        "I can help you understand your data governance ecosystem. Try asking:\n",
        "- **\"What's at risk?\"** — risk analysis across sources and reports",
        "- **\"Summarize the dashboard\"** — overview of all sources and reports",
        "- **\"Show stale sources\"** — list sources with aging data",
        f"- **\"Tell me about [report name]\"** — details on any of your {len(reports)} reports",
        "- **\"What has no connection?\"** — SQL sources that can't be probed",
        "- **\"Show alerts\"** — active alerts",
        "- **\"Show actions\"** — open action items",
    ]
    return {"response": "\n".join(lines), "sources_referenced": [], "reports_referenced": []}


# ── Briefing mock ──

def mock_briefing(summary: dict) -> dict:
    """Generate a dashboard briefing from real data."""
    sc = summary["status_counts"]
    total = summary["sources_total"]
    reports = summary["reports"]
    scan = summary.get("last_scan")
    scan_ago = _time_ago(scan.get("started_at")) if scan else "never"
    alerts = summary["alerts_active"]

    sources_list = summary["sources"]
    no_conn = [s for s in sources_list if s.get("probe_status") == "no_connection"]
    at_risk = [s for s in sources_list if s.get("probe_status") == "stale"]
    degraded = [s for s in sources_list if s.get("probe_status") == "outdated"]
    healthy = [s for s in sources_list if s.get("probe_status") == "fresh"]

    parts = []
    if total == 0:
        parts.append("No data sources have been scanned yet. Run a scan to start monitoring your BI ecosystem.")
        risk = "low"
    else:
        # Opening summary
        if healthy:
            parts.append(f"Your BI ecosystem is tracking **{total} data sources** across **{len(reports)} reports**. "
                         f"**{len(healthy)}** of {total} sources are healthy")
            if no_conn:
                names = ", ".join(_short_name(s["name"]) for s in no_conn[:4])
                more = f" and {len(no_conn) - 4} more" if len(no_conn) > 4 else ""
                parts[-1] += f", but **{len(no_conn)} SQL sources have no connection** ({names}{more}) — these cannot be probed for freshness."
            else:
                parts[-1] += "."
        else:
            parts.append(f"Tracking **{total} sources** and **{len(reports)} reports**.")

        # Issues
        if at_risk:
            names = ", ".join(_short_name(s["name"]) for s in at_risk[:3])
            parts.append(f"**{len(at_risk)} at-risk source{'s' if len(at_risk) != 1 else ''}** (data 31-90 days old): {names}.")
        if degraded:
            names = ", ".join(_short_name(s["name"]) for s in degraded[:3])
            parts.append(f"**{len(degraded)} degraded source{'s' if len(degraded) != 1 else ''}** (data >90 days old): {names}.")

        if alerts:
            parts.append(f"**{alerts} active alert{'s' if alerts != 1 else ''}** need attention.")
        elif not at_risk and not degraded and not no_conn:
            parts.append("No active alerts.")

        parts.append(f"Last scan: **{scan_ago}**.")

        # Risk level — any at_risk/degraded = high; any no_connection = medium; all healthy = low
        if at_risk or degraded:
            risk = "high"
        elif no_conn:
            risk = "medium"
        else:
            risk = "low"

    return {
        "summary": " ".join(parts),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_level": risk,
    }


# ── Report risk mock ──

def mock_report_risk(report_ctx: dict) -> dict:
    """Generate risk assessment for a specific report."""
    report = report_ctx.get("report", {})
    tables = report_ctx.get("tables", [])
    shared = report_ctx.get("shared_reports", [])

    if not tables:
        return {
            "risk_level": "low",
            "assessment": f"**{report.get('name', 'Unknown')}** has no linked data sources. Risk is low but data lineage is incomplete — run a scan to detect sources.",
            "at_risk_sources": [],
        }

    healthy = [t for t in tables if t.get("probe_status") == "fresh"]
    at_risk_src = [t for t in tables if t.get("probe_status") == "stale"]
    no_conn = [t for t in tables if t.get("probe_status") == "no_connection"]
    degraded = [t for t in tables if t.get("probe_status") == "outdated"]
    unknown = [t for t in tables if t.get("probe_status") in (None, "unknown")]

    unique_sources = {t["source_id"] for t in tables if t.get("source_id")}
    at_risk = []
    parts = []

    if no_conn:
        risk = "high"
        names = ", ".join(f"**{_short_name(t.get('source_name', ''))}**" for t in no_conn)
        parts.append(f"This report has **HIGH risk**. {len(no_conn)} of {len(unique_sources)} sources ({names}) have no SQL connection. "
                     f"If this persists, the report will show degraded or missing data.")
        at_risk = [{"source_id": t["source_id"], "source_name": t.get("source_name"), "reason": "no_connection"} for t in no_conn]
    elif degraded:
        risk = "high"
        names = ", ".join(f"**{_short_name(t.get('source_name', ''))}**" for t in degraded)
        parts.append(f"This report has **HIGH risk**. {len(degraded)} source{'s are' if len(degraded) != 1 else ' is'} degraded ({names}) with data older than 90 days.")
        at_risk = [{"source_id": t["source_id"], "source_name": t.get("source_name"), "reason": "degraded"} for t in degraded]
    elif at_risk_src:
        risk = "medium"
        names = ", ".join(f"**{_short_name(t.get('source_name', ''))}**" for t in at_risk_src)
        parts.append(f"This report has **MEDIUM risk**. {len(at_risk_src)} source{'s have' if len(at_risk_src) != 1 else ' has'} at-risk data ({names}).")
        at_risk = [{"source_id": t["source_id"], "source_name": t.get("source_name"), "reason": "at_risk"} for t in at_risk_src]
    elif unknown and not healthy:
        risk = "medium"
        parts.append(f"This report has **MEDIUM risk**. None of its {len(unique_sources)} sources have been probed yet — run a probe to verify freshness.")
    else:
        risk = "low"
        parts.append(f"This report has **LOW risk**. All {len(healthy)} source{'s are' if len(healthy) != 1 else ' is'} healthy and updating regularly.")

    if shared:
        shared_names = ", ".join(r["name"] for r in shared[:4])
        parts.append(f"Sources are shared with {len(shared)} other report{'s' if len(shared) != 1 else ''}: {shared_names}.")

    return {
        "risk_level": risk,
        "assessment": " ".join(parts),
        "at_risk_sources": at_risk,
    }


# ── Suggestions mock ──

def mock_suggestions(summary: dict) -> dict:
    """Generate actionable suggestions based on current state."""
    sc = summary["status_counts"]
    sources = summary["sources"]
    reports = summary["reports"]
    suggestions = []

    no_conn = [s for s in sources if s.get("probe_status") == "no_connection"]
    at_risk_src = [s for s in sources if s.get("probe_status") == "stale"]
    degraded_src = [s for s in sources if s.get("probe_status") == "outdated"]
    no_freq = [r for r in reports if not r.get("frequency")]

    if no_conn:
        names = ", ".join(_short_name(s["name"]) for s in no_conn[:4])
        suggestions.append({
            "title": "Investigate SQL connection issues",
            "description": f"{len(no_conn)} SQL sources have no connection ({names}). This may indicate a server connectivity problem or misconfigured credentials. Verify the SQL Server is reachable from this environment.",
            "priority": "high",
            "related_entity": "source",
            "entity_id": no_conn[0].get("id") if no_conn else None,
        })

    if degraded_src:
        names = ", ".join(_short_name(s["name"]) for s in degraded_src[:3])
        suggestions.append({
            "title": "Address degraded data sources",
            "description": f"{len(degraded_src)} sources have data older than 90 days ({names}). Review whether these sources are still active or should be decommissioned.",
            "priority": "high",
            "related_entity": "source",
            "entity_id": degraded_src[0].get("id") if degraded_src else None,
        })

    if at_risk_src:
        names = ", ".join(_short_name(s["name"]) for s in at_risk_src[:3])
        suggestions.append({
            "title": "Refresh at-risk data sources",
            "description": f"{len(at_risk_src)} sources have data 31-90 days old ({names}). Check refresh schedules and ensure ETL pipelines are running.",
            "priority": "medium",
            "related_entity": "source",
            "entity_id": at_risk_src[0].get("id") if at_risk_src else None,
        })

    if no_freq:
        names = ", ".join(r["name"] for r in no_freq[:3])
        more = f" and {len(no_freq) - 3} more" if len(no_freq) > 3 else ""
        suggestions.append({
            "title": "Set report refresh frequencies",
            "description": f"{len(no_freq)} reports have no frequency set ({names}{more}). Setting frequencies helps track whether reports are being refreshed on schedule.",
            "priority": "medium",
            "related_entity": "report",
            "entity_id": no_freq[0].get("id") if no_freq else None,
        })

    if summary["actions_open"] > 0:
        suggestions.append({
            "title": "Resolve open action items",
            "description": f"{summary['actions_open']} open actions are pending. Review and triage them — mark expected items as 'expected' to reduce noise.",
            "priority": "medium",
            "related_entity": None,
            "entity_id": None,
        })

    # Always offer a positive suggestion if things are mostly healthy
    if len(suggestions) < 2:
        suggestions.append({
            "title": "Schedule regular probes",
            "description": "Set up automated probing to catch freshness issues early. Run probes daily before business hours so stakeholders see up-to-date status each morning.",
            "priority": "low",
            "related_entity": None,
            "entity_id": None,
        })

    if not suggestions:
        suggestions.append({
            "title": "Ecosystem is healthy",
            "description": "No immediate actions needed. All sources are fresh and reports are current. Consider documenting your data lineage for new team members.",
            "priority": "low",
            "related_entity": None,
            "entity_id": None,
        })

    return {"suggestions": suggestions[:5]}
