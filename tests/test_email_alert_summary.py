from app.routers.email import _build_alert_summary
from app.routers.email_schedules import _normalize_content_types


def test_alert_email_is_a_single_actionable_table_with_degradation_evidence():
    alerts = [
        {
            "type": "refresh_failed",
            "asset_type": "report",
            "asset_name": "Revenue <Model>",
            "created_at": "2026-08-10T07:30:00+00:00",
            "degraded_since": "2026-08-10T07:30:00+00:00",
            "triage_score": 240900,
            "impact_views_30d": 240,
            "report_links": [{"report_id": 4, "report_name": "Revenue", "powerbi_url": "https://example.test/report"}],
            "source_links": [],
            "recommendation": "Fix the gateway and retry the refresh.",
            "pbi_refresh_error": "Gateway <cluster> could not reach the source.",
            "report_detail": {
                "id": 4,
                "name": "Revenue <Model>",
                "powerbi_url": "https://example.test/report",
                "pbi_refresh_error": "Gateway <cluster> could not reach the source.",
            },
        },
        {
            "type": "stale_source",
            "asset_type": "source",
            "asset_name": "Orders.xlsx",
            "created_at": "2026-08-02T12:00:00+00:00",
            "degraded_since": "2026-08-02T12:00:00+00:00",
            "triage_score": 760,
            "impact_views_30d": 0,
            "report_links": [],
            "source_links": [],
            "source_detail": {"id": 7, "name": "Orders.xlsx", "type": "excel"},
            "triage_cta": "Open source",
        },
    ]

    summary = _build_alert_summary({"name": "Owner One", "email": "owner@example.test"}, alerts)

    assert summary["subject"].startswith("Metronome alerts - ")
    assert "2 active alerts need attention" in summary["subject"]
    assert "Owner One" in summary["subject"]
    assert "WHAT TO DO FIRST" not in summary["body_text"]
    assert "What to do first" not in summary["body_html"]
    assert "All active alerts" not in summary["body_html"]
    assert "Alert details" not in summary["body_html"]
    assert "Artifact | Issue and name | Degraded since | Views" in summary["body_text"]
    assert "Artifact</th>" in summary["body_html"]
    assert "Degraded since</th>" in summary["body_html"]
    assert "Views</th>" in summary["body_html"]
    assert "weighted" not in summary["body_text"].casefold()
    assert "weighted" not in summary["body_html"].casefold()
    assert "PBI Refresh Error:" in summary["body_text"]
    assert "Gateway &lt;cluster&gt; could not reach the source." in summary["body_html"]
    assert "Revenue &lt;Model&gt;" in summary["body_html"]
    assert "Revenue <Model>" not in summary["body_html"]
    assert "10 Aug 2026" in summary["body_html"]
    assert "Power BI" in summary["body_html"]
    assert "Excel" in summary["body_html"]
    assert "Next action:" in summary["body_text"]
    assert summary["alerts"][0]["artifact_kind"] == "powerbi"
    assert summary["mailto"].startswith("mailto:owner%40example.test")


def test_owner_schedule_content_is_alert_only():
    assert _normalize_content_types(["tasks", "alerts"]) == ["alerts"]
