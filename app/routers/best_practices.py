"""API router for the Power BI best-practice checker."""

from fastapi import APIRouter

from app.config import TMDL_ROOT
from app.checks.best_practices import scan_all

router = APIRouter(prefix="/api/best-practices", tags=["best-practices"])


@router.get("")
def get_findings():
    """Scan all reports and return best-practice findings."""
    findings = scan_all(TMDL_ROOT)

    # Also scan test_reports if TMDL_ROOT points to test_data (covers both dirs)
    from pathlib import Path
    test_reports = Path(TMDL_ROOT).parent / "test_reports"
    if test_reports.is_dir():
        findings.extend(scan_all(test_reports))

    # Also check a nested "reports" subdirectory (test_data/reports layout)
    nested = Path(TMDL_ROOT) / "reports"
    if nested.is_dir():
        findings.extend(scan_all(nested))

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
