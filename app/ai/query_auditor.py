"""Mock audit of Power Query M expressions for data quality issues."""

import re


def _short_name(full_name: str) -> str:
    if not full_name:
        return ""
    normalized = full_name.replace("\\", "/")
    last_slash = normalized.rfind("/")
    base = normalized[last_slash + 1:] if last_slash >= 0 else normalized
    if "." in base and not re.search(r"\.(csv|xlsx|xls|json|parquet|txt)$", base, re.I):
        return base
    dot = base.rfind(".")
    return base[:dot] if dot > 0 else base


def mock_audit_report(report: dict, tables: list) -> dict:
    """Audit M expressions for a single report, returning findings."""
    findings = []
    tables_with_expr = [t for t in tables if t.get("source_expression")]

    for t in tables_with_expr:
        expr = t["source_expression"]
        table_name = t.get("table_name", "Unknown")

        # Check for hardcoded Windows paths
        win_paths = re.findall(r'[A-Z]:\\[^\s"\')\]]+', expr)
        for path in win_paths:
            findings.append({
                "severity": "high",
                "category": "Hardcoded Path",
                "table": table_name,
                "detail": f"Hardcoded Windows path found: `{path}`. This will break if the file moves or when deployed to a different environment.",
                "snippet": path,
            })

        # Check for hardcoded UNC paths
        unc_paths = re.findall(r'\\\\[^\s"\')\]]+', expr)
        for path in unc_paths:
            findings.append({
                "severity": "medium",
                "category": "UNC Path",
                "table": table_name,
                "detail": f"UNC network path found: `{path}`. Consider parameterizing the server path.",
                "snippet": path,
            })

        # Check for hardcoded server names in connection strings
        servers = re.findall(r'[Ss]ource\s*=\s*["\']?([a-zA-Z0-9._-]+(?:\\[a-zA-Z0-9_]+)?)', expr)
        for srv in servers:
            if srv.lower() not in ("localhost", ".", "query", "table", "true", "false", "null"):
                findings.append({
                    "severity": "low",
                    "category": "Hardcoded Server",
                    "table": table_name,
                    "detail": f"Server name `{srv}` is hardcoded. Consider using a parameter or environment variable for portability.",
                    "snippet": srv,
                })

        # Check for plaintext credentials
        if re.search(r'[Pp]assword\s*=\s*["\'][^"\']+["\']', expr):
            findings.append({
                "severity": "high",
                "category": "Exposed Credentials",
                "table": table_name,
                "detail": "Password appears to be hardcoded in the connection string. Use integrated auth or a credential store.",
                "snippet": "Password=***",
            })

        # Check for missing error handling (no try/otherwise pattern)
        if len(expr) > 100 and "try" not in expr.lower() and "otherwise" not in expr.lower():
            findings.append({
                "severity": "low",
                "category": "No Error Handling",
                "table": table_name,
                "detail": f"Table `{table_name}` has a complex query with no try/otherwise error handling. Consider wrapping data access in error handling.",
                "snippet": "",
            })

    # Check for sources with no connection
    no_conn_tables = [t for t in tables if t.get("probe_status") == "no_connection"]
    for t in no_conn_tables:
        src_name = _short_name(t.get("source_name", ""))
        findings.append({
            "severity": "high",
            "category": "Connection Failure",
            "table": t.get("table_name", "Unknown"),
            "detail": f"Source `{src_name}` has no active connection. The SQL Server may be unreachable or credentials may have expired.",
            "snippet": "",
        })

    # Check for tables with no M expression at all
    no_expr = [t for t in tables if not t.get("source_expression")]
    if no_expr:
        names = ", ".join(t.get("table_name", "?") for t in no_expr[:3])
        more = f" and {len(no_expr) - 3} more" if len(no_expr) > 3 else ""
        findings.append({
            "severity": "low",
            "category": "Missing Expression",
            "table": names,
            "detail": f"No M expression found for {len(no_expr)} table(s) ({names}{more}). These may be calculated tables or the scan didn't capture expressions.",
            "snippet": "",
        })

    # Sort by severity
    sev_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: sev_order.get(f["severity"], 3))

    # Summary
    high = len([f for f in findings if f["severity"] == "high"])
    medium = len([f for f in findings if f["severity"] == "medium"])
    low = len([f for f in findings if f["severity"] == "low"])

    if high:
        summary = f"Found **{high} high-severity** issue{'s' if high != 1 else ''}"
        if medium:
            summary += f", {medium} medium"
        if low:
            summary += f", {low} low"
        summary += f" across {len(tables)} tables."
    elif medium:
        summary = f"Found {medium} medium-severity and {low} low-severity issues. No critical problems."
    elif low:
        summary = f"Found {low} minor issue{'s' if low != 1 else ''}. Queries look generally healthy."
    else:
        summary = f"No issues found across {len(tables)} tables. Queries look clean."

    return {
        "report_name": report.get("name", "Unknown"),
        "summary": summary,
        "findings": findings,
        "tables_audited": len(tables_with_expr),
        "tables_total": len(tables),
    }


def mock_audit_all(reports_data: list) -> dict:
    """Audit all reports and return aggregated findings."""
    all_findings = []
    report_summaries = []

    for rd in reports_data:
        result = mock_audit_report(rd["report"], rd["tables"])
        high = len([f for f in result["findings"] if f["severity"] == "high"])
        report_summaries.append({
            "report_name": result["report_name"],
            "findings_count": len(result["findings"]),
            "high_count": high,
        })
        for f in result["findings"]:
            f["report_name"] = result["report_name"]
            all_findings.append(f)

    # Sort by severity
    sev_order = {"high": 0, "medium": 1, "low": 2}
    all_findings.sort(key=lambda f: sev_order.get(f["severity"], 3))

    total_high = sum(r["high_count"] for r in report_summaries)
    total_findings = sum(r["findings_count"] for r in report_summaries)
    reports_with_issues = len([r for r in report_summaries if r["findings_count"] > 0])

    lines = [f"## Audit Summary\n"]
    lines.append(f"Audited **{len(report_summaries)} reports**. Found **{total_findings} issues** ({total_high} high-severity) across **{reports_with_issues} reports**.\n")

    if total_high:
        lines.append("### High-Severity Findings\n")
        for f in all_findings:
            if f["severity"] == "high":
                lines.append(f"- **{f['report_name']}** / {f['table']}: {f['detail']}")

    if not all_findings:
        lines.append("All queries look clean across all reports.")

    return {
        "response": "\n".join(lines),
        "total_findings": total_findings,
        "reports_audited": len(report_summaries),
    }
