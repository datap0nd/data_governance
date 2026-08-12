"""API router for the Power BI best-practice checker."""

import re
from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import TMDL_ROOT
from app.database import get_db
from app.checks.best_practices import scan_all
from app.scanner.findings import sync_managed_actions

router = APIRouter(prefix="/api/best-practices", tags=["best-practices"])


def _db_findings() -> list[dict]:
    """Run DB-backed best-practice checks against stored metadata."""
    findings: list[dict] = []

    with get_db() as db:
        # 1. Measure bloat — reports with too many measures
        measure_counts = db.execute("""
            SELECT rm.report_id, r.name AS report_name,
                   COUNT(*) AS measure_count,
                   COUNT(DISTINCT rm.table_name) AS table_count
            FROM report_measures rm
            JOIN reports r ON r.id = rm.report_id
            GROUP BY rm.report_id
            HAVING COUNT(*) > 50
        """).fetchall()
        for mc in measure_counts:
            cnt = mc["measure_count"]
            tables = mc["table_count"]
            sev = "high" if cnt > 100 else "medium"
            spread = f" spread across {tables} tables" if tables > 1 else ""
            findings.append({
                "report": mc["report_name"],
                "table": "—",
                "rule": "Measure bloat",
                "issue": f'Report has {cnt} measures{spread}. Large measure counts slow refresh, increase model size, and make reports harder to maintain. Consider splitting into a shared dataset.',
                "severity": sev,
            })

        # 2. Visual density — pages with > 15 visuals
        pages = db.execute("""
            SELECT rp.id, rp.page_name, r.name AS report_name,
                   COUNT(rv.id) AS visual_count
            FROM report_pages rp
            JOIN reports r ON r.id = rp.report_id
            LEFT JOIN report_visuals rv ON rv.page_id = rp.id
            GROUP BY rp.id
            HAVING COUNT(rv.id) > 15
        """).fetchall()
        for p in pages:
            sev = "medium" if p["visual_count"] > 25 else "low"
            findings.append({
                "report": p["report_name"],
                "table": f'Page: {p["page_name"]}',
                "rule": "Too many visuals on page",
                "issue": f'Page has {p["visual_count"]} visuals. Pages with many visuals are slower to render and harder to read. Consider splitting into multiple pages.',
                "severity": sev,
            })

        # 3. Hardcoded values in DAX
        dax_measures = db.execute("""
            SELECT rm.report_id, r.name AS report_name, rm.table_name,
                   rm.measure_name, rm.measure_dax
            FROM report_measures rm
            JOIN reports r ON r.id = rm.report_id
            WHERE rm.measure_dax IS NOT NULL AND rm.measure_dax != ''
        """).fetchall()
        date_pattern = re.compile(r'DATE\s*\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*\)', re.IGNORECASE)
        quoted_date_pattern = re.compile(r'"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"')
        for m in dax_measures:
            dax = m["measure_dax"]
            if date_pattern.search(dax) or quoted_date_pattern.search(dax):
                findings.append({
                    "report": m["report_name"],
                    "table": m["table_name"],
                    "rule": "Hardcoded date in DAX",
                    "issue": f'Measure "{m["measure_name"]}" contains a hardcoded date. Use dynamic date functions (TODAY, NOW) or a date parameter table instead.',
                    "severity": "medium",
                })

    return findings


def run_best_practice_scan(*, persist: bool = True) -> dict:
    """Scan reports and optionally synchronize one owned action per report."""
    # File-based checks (TMDL/PBIX)
    tmdl_findings = scan_all(TMDL_ROOT)

    # DB-based checks (stored metadata)
    db_findings = _db_findings()

    combined = [
        {
            "report": f.report,
            "table": f.table,
            "rule": f.rule,
            "issue": f.issue,
            "severity": f.severity,
        }
        for f in tmdl_findings
    ] + db_findings

    lifecycle = None
    if persist:
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            report_rows = db.execute(
                "SELECT id, name, owner FROM reports WHERE COALESCE(archived, 0) = 0"
            ).fetchall()
            report_map = {row["name"].strip().casefold(): row for row in report_rows}
            grouped: dict[int, dict] = {}
            for finding in combined:
                report = report_map.get((finding.get("report") or "").strip().casefold())
                if not report:
                    continue
                bucket = grouped.setdefault(report["id"], {"report": report, "findings": []})
                bucket["findings"].append(finding)

            managed = []
            for report_id, bucket in grouped.items():
                report = bucket["report"]
                items = sorted(
                    bucket["findings"],
                    key=lambda item: ({"high": 0, "medium": 1, "low": 2}.get(item.get("severity"), 3), item.get("rule") or ""),
                )
                details = "; ".join(
                    f"{item['rule']} ({item.get('table') or 'report'}): {item['issue']}"
                    for item in items[:8]
                )
                if len(items) > 8:
                    details += f"; plus {len(items) - 8} more finding(s)"
                managed.append({
                    "fingerprint": f"best_practice:{report_id}",
                    "report_id": report_id,
                    "assigned_to": report["owner"],
                    "notes": f"{len(items)} TMDL best-practice finding(s). {details}"[:4000],
                })
            lifecycle = sync_managed_actions(db, "best_practice", managed, now)

    return {
        "total": len(combined),
        "findings": combined,
        "actions": lifecycle,
    }


@router.get("")
def get_findings():
    """Scan all reports, persist current findings, and return them."""
    return run_best_practice_scan(persist=True)
