"""API router for the Power BI best-practice checker."""

import re

from fastapi import APIRouter

from app.config import TMDL_ROOT
from app.database import get_db
from app.checks.best_practices import scan_all

router = APIRouter(prefix="/api/best-practices", tags=["best-practices"])


def _db_findings() -> list[dict]:
    """Run DB-backed best-practice checks against stored metadata."""
    findings: list[dict] = []

    with get_db() as db:
        # 1. Unused measures — measures not referenced by any visual
        report_measures = db.execute("""
            SELECT rm.report_id, r.name AS report_name, rm.table_name, rm.measure_name
            FROM report_measures rm
            JOIN reports r ON r.id = rm.report_id
        """).fetchall()
        for rm in report_measures:
            used = db.execute("""
                SELECT 1 FROM visual_fields vf
                JOIN report_visuals rv ON rv.id = vf.visual_id
                JOIN report_pages rp ON rp.id = rv.page_id
                WHERE rp.report_id = ? AND vf.table_name = ? AND vf.field_name = ?
                LIMIT 1
            """, (rm["report_id"], rm["table_name"], rm["measure_name"])).fetchone()
            if not used:
                findings.append({
                    "report": rm["report_name"],
                    "table": rm["table_name"],
                    "rule": "Unused measure",
                    "issue": f'Measure "{rm["measure_name"]}" is defined but not used in any visual. Remove it or verify it is needed by other measures.',
                    "severity": "medium",
                })

        # 2. Unused columns — columns not referenced by any visual
        report_columns = db.execute("""
            SELECT rc.report_id, r.name AS report_name, rc.table_name, rc.column_name
            FROM report_columns rc
            JOIN reports r ON r.id = rc.report_id
        """).fetchall()
        for rc in report_columns:
            used = db.execute("""
                SELECT 1 FROM visual_fields vf
                JOIN report_visuals rv ON rv.id = vf.visual_id
                JOIN report_pages rp ON rp.id = rv.page_id
                WHERE rp.report_id = ? AND vf.table_name = ? AND vf.field_name = ?
                LIMIT 1
            """, (rc["report_id"], rc["table_name"], rc["column_name"])).fetchone()
            if not used:
                findings.append({
                    "report": rc["report_name"],
                    "table": rc["table_name"],
                    "rule": "Unused column",
                    "issue": f'Column "{rc["column_name"]}" is imported but not used in any visual. Removing unused columns reduces model size and improves refresh speed.',
                    "severity": "low",
                })

        # 3. Visual density — pages with > 15 visuals
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

        # 4. Missing visual titles
        untitled = db.execute("""
            SELECT rv.visual_type, rp.page_name, r.name AS report_name
            FROM report_visuals rv
            JOIN report_pages rp ON rp.id = rv.page_id
            JOIN reports r ON r.id = rp.report_id
            WHERE (rv.title IS NULL OR rv.title = '')
              AND rv.visual_type NOT IN ('textbox', 'slicer', 'shape', 'image', 'actionButton')
        """).fetchall()
        for v in untitled:
            findings.append({
                "report": v["report_name"],
                "table": f'Page: {v["page_name"]}',
                "rule": "Missing visual title",
                "issue": f'A {v["visual_type"]} visual has no title. Add descriptive titles so users understand what each chart shows.',
                "severity": "low",
            })

        # 7. Hardcoded values in DAX
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


@router.get("")
def get_findings():
    """Scan all reports and return best-practice findings."""
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

    return {
        "total": len(combined),
        "findings": combined,
    }
