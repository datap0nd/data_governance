"""System prompts and context builders for AI features."""

import json
from app.ai.context_builder import get_dashboard_summary, get_full_context, get_report_context

SYSTEM_PROMPT = """You are a data governance analyst. You review Power BI report metadata, data source freshness, and lineage to identify risks and recommend actions. Be concise and actionable. Format responses in markdown. Reference specific source names, report names, and statuses from the data provided."""

BRIEFING_SYSTEM = SYSTEM_PROMPT + """
Generate a 2-3 sentence executive briefing about the current state of the BI ecosystem. Mention specific numbers and source names. Note any critical issues first."""

CHAT_SYSTEM = SYSTEM_PROMPT + """
Answer the user's question about their data governance ecosystem using the context data provided. Be helpful, specific, and reference actual source/report names."""

RISK_SYSTEM = SYSTEM_PROMPT + """
Assess the data quality risk for the given Power BI report. Rate as LOW, MEDIUM, or HIGH risk. Explain which sources are at risk and why. Be specific."""


def build_briefing_prompt() -> str:
    """Build a user prompt with real dashboard data for briefing generation."""
    summary = get_dashboard_summary()
    sc = summary["status_counts"]
    sources = summary["sources"]
    stale = [s["name"].replace("\\", "/").split("/")[-1] for s in sources if s.get("probe_status") == "stale"]
    outdated = [s["name"].replace("\\", "/").split("/")[-1] for s in sources if s.get("probe_status") == "outdated"]
    no_conn = [s["name"].replace("\\", "/").split("/")[-1] for s in sources if s.get("probe_status") == "no_connection"]

    return f"""Current ecosystem state:
- {summary['sources_total']} total data sources, {len(summary['reports'])} reports
- Fresh: {sc.get('fresh', 0)}, Stale: {sc.get('stale', 0)}, Outdated: {sc.get('outdated', 0)}, No Connection: {sc.get('no_connection', 0)}, Unknown: {sc.get('unknown', 0)}
- Stale sources: {', '.join(stale) if stale else 'none'}
- Outdated sources: {', '.join(outdated) if outdated else 'none'}
- No connection: {', '.join(no_conn) if no_conn else 'none'}
- Active alerts: {summary['alerts_active']}
- Open actions: {summary['actions_open']}
- Reports without refresh frequency: {summary['reports_without_freq']}

Generate a concise executive briefing."""


def build_chat_prompt(user_message: str) -> str:
    """Build a user prompt with full context for chat."""
    ctx = get_full_context()

    # Summarize sources by status
    status_groups = {}
    for s in ctx["sources"]:
        st = s.get("probe_status") or "unknown"
        if st not in status_groups:
            status_groups[st] = []
        name = s["name"].replace("\\", "/").split("/")[-1]
        status_groups[st].append(name)

    sources_summary = "\n".join(
        f"  {status}: {', '.join(names[:10])}{' ...' if len(names) > 10 else ''}"
        for status, names in status_groups.items()
    )

    reports_summary = ", ".join(r["name"] for r in ctx["reports"])
    alerts_count = len(ctx["alerts"])
    actions_open = len([a for a in ctx["actions"] if a.get("status") == "open"])

    return f"""Data context:
Sources ({len(ctx['sources'])} total):
{sources_summary}

Reports: {reports_summary}
Active alerts: {alerts_count}, Open actions: {actions_open}

User question: {user_message}"""


def build_risk_prompt(report_id: int) -> str:
    """Build a prompt for report risk assessment."""
    ctx = get_report_context(report_id)
    if not ctx:
        return "Report not found."

    report = ctx["report"]
    tables = ctx["tables"]

    table_details = []
    for t in tables:
        src_name = (t.get("source_name") or "unknown").replace("\\", "/").split("/")[-1]
        status = t.get("probe_status") or "unknown"
        table_details.append(f"  - {t['table_name']} -> {src_name} ({status})")

    shared = ctx.get("shared_reports", [])
    shared_names = ", ".join(r["name"] for r in shared) if shared else "none"

    return f"""Report: {report['name']}
Owner: {report.get('owner') or 'not set'}
Business owner: {report.get('business_owner') or 'not set'}

Tables and sources:
{chr(10).join(table_details) if table_details else '  No tables found'}

Shared sources with: {shared_names}

Assess the data quality risk for this report."""
