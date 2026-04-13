"""System prompts and templates for AI features."""

CHAT_SYSTEM = """You are the AI assistant for MX Analytics, a platform that monitors a Power BI ecosystem.
You have access to real-time data about {source_count} data sources and {report_count} reports.
You help users understand data health, identify risks, and suggest improvements.
Respond in concise markdown. Reference specific source names, report names, and statuses."""

BRIEFING_TEMPLATE = """Summarize the current state of the BI ecosystem in 2-3 sentences.
Data: {sources_fresh} healthy, {sources_outdated} degraded, {sources_unknown} unknown out of {sources_total} sources.
{reports_total} reports. {alerts_active} active alerts. Last scan: {last_scan_ago}.
Problem sources: {problem_sources}
Problem reports: {problem_reports}"""

REPORT_RISK_TEMPLATE = """Assess the data quality risk for report "{report_name}".
Sources: {sources_detail}
Overall: {fresh_count} healthy, {no_conn_count} no connection, {unknown_count} unknown."""

SUGGESTIONS_TEMPLATE = """Given the current state, suggest 3-5 actionable improvements.
State: {sources_total} sources ({no_conn_count} with no connection, {outdated_count} degraded).
{reports_without_freq} reports have no frequency set.
{open_actions} open actions. {active_alerts} active alerts.
Notable: {notable_items}"""
