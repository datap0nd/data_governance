"""Gathers app data from the database into context dicts for the AI providers."""

from app.database import get_db


def get_full_context() -> dict:
    """Gather all sources, reports, alerts, actions for AI context."""
    with get_db() as db:
        sources = [dict(r) for r in db.execute("""
            SELECT s.*,
                   sp.status AS probe_status,
                   CAST(sp.last_data_at AS TEXT) AS last_updated
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
        """).fetchall()]

        reports = [dict(r) for r in db.execute("""
            SELECT r.*,
                   (SELECT COUNT(DISTINCT rt.source_id)
                    FROM report_tables rt WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
                   ) AS source_count
            FROM reports r ORDER BY r.name
        """).fetchall()]

        alerts = [dict(r) for r in db.execute(
            "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY created_at DESC LIMIT 20"
        ).fetchall()]

        actions = [dict(r) for r in db.execute(
            "SELECT a.*, s.name AS source_name, r.name AS report_name "
            "FROM actions a LEFT JOIN sources s ON s.id = a.source_id "
            "LEFT JOIN reports r ON r.id = a.report_id ORDER BY a.created_at DESC"
        ).fetchall()]

        last_scan = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        # Report-to-sources mapping
        edges = [dict(r) for r in db.execute("""
            SELECT DISTINCT rt.report_id, rt.source_id, s.name AS source_name,
                   s.type AS source_type
            FROM report_tables rt
            JOIN sources s ON s.id = rt.source_id
        """).fetchall()]

    return {
        "sources": sources,
        "reports": reports,
        "alerts": alerts,
        "actions": actions,
        "last_scan": dict(last_scan) if last_scan else None,
        "edges": edges,
    }


def get_report_context(report_id: int) -> dict:
    """Gather data for a specific report and its sources."""
    with get_db() as db:
        report = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not report:
            return {}

        tables = [dict(r) for r in db.execute("""
            SELECT rt.*, s.name AS source_name, s.type AS source_type,
                   s.connection_info, s.source_query,
                   sp.status AS probe_status,
                   CAST(sp.last_data_at AS TEXT) AS last_updated
            FROM report_tables rt
            LEFT JOIN sources s ON s.id = rt.source_id
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = rt.source_id AND sp.rn = 1
            WHERE rt.report_id = ?
            ORDER BY rt.table_name
        """, (report_id,)).fetchall()]

        # Other reports sharing the same sources
        source_ids = [t["source_id"] for t in tables if t.get("source_id")]
        shared_reports = []
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            shared_reports = [dict(r) for r in db.execute(f"""
                SELECT DISTINCT r.id, r.name
                FROM report_tables rt
                JOIN reports r ON r.id = rt.report_id
                WHERE rt.source_id IN ({placeholders}) AND rt.report_id != ?
            """, source_ids + [report_id]).fetchall()]

    return {
        "report": dict(report),
        "tables": tables,
        "shared_reports": shared_reports,
    }


def get_dashboard_summary() -> dict:
    """Get summary stats for briefing."""
    with get_db() as db:
        sources = [dict(r) for r in db.execute("""
            SELECT s.name, s.type, s.connection_info,
                   sp.status AS probe_status,
                   CAST(sp.last_data_at AS TEXT) AS last_updated
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
        """).fetchall()]

        reports = [dict(r) for r in db.execute("""
            SELECT r.name, r.owner, r.frequency,
                   (SELECT COUNT(DISTINCT rt.source_id)
                    FROM report_tables rt WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
                   ) AS source_count
            FROM reports r
        """).fetchall()]

        alerts_active = db.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE acknowledged = 0"
        ).fetchone()["c"]

        last_scan = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        actions_open = db.execute(
            "SELECT COUNT(*) AS c FROM actions WHERE status = 'open'"
        ).fetchone()["c"]

    status_counts = {"healthy": 0, "at_risk": 0, "degraded": 0, "no_connection": 0, "unknown": 0}
    _status_map = {"fresh": "healthy", "stale": "at_risk", "outdated": "degraded"}
    for s in sources:
        st = s.get("probe_status") or "unknown"
        mapped = _status_map.get(st, st)
        if mapped in status_counts:
            status_counts[mapped] += 1
        else:
            status_counts["unknown"] += 1

    return {
        "sources": sources,
        "reports": reports,
        "sources_total": len(sources),
        "status_counts": status_counts,
        "alerts_active": alerts_active,
        "actions_open": actions_open,
        "last_scan": dict(last_scan) if last_scan else None,
        "reports_without_freq": len([r for r in reports if not r.get("frequency")]),
    }
