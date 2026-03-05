"""API router for the Power BI best-practice checker."""

from fastapi import APIRouter

from app.config import TMDL_ROOT
from app.checks.best_practices import scan_all

router = APIRouter(prefix="/api/best-practices", tags=["best-practices"])


@router.get("")
def get_findings():
    """Scan all reports and return best-practice findings."""
    findings = scan_all(TMDL_ROOT)

    return {
        "total": len(findings),
        "findings": [
            {
                "report": f.report,
                "table": f.table,
                "rule": f.rule,
                "issue": f.issue,
                "severity": f.severity,
            }
            for f in findings
        ],
    }
