from app.routers.email import _build_alert_summary
from app.routers.email_schedules import _normalize_content_types


def test_alert_email_is_ranked_actionable_and_outlook_safe():
    alerts = [
        {
            "type": "refresh_failed",
            "asset_name": "Revenue <Model>",
            "asset_days": 2,
            "impact_views_30d": 240,
            "report_links": [{"report_id": 4, "report_name": "Revenue", "powerbi_url": "https://example.test/report"}],
            "source_links": [],
            "recommendation": "Check the gateway and retry the refresh.",
            "report_detail": {"id": 4, "name": "Revenue <Model>", "powerbi_url": "https://example.test/report"},
        },
        {
            "type": "stale_source",
            "asset_name": "Orders.xlsx",
            "asset_days": 12,
            "impact_views_30d": 0,
            "report_links": [],
            "source_links": [],
            "triage_cta": "Open source",
        },
    ]

    summary = _build_alert_summary({"name": "Owner One", "email": "owner@example.test"}, alerts)

    assert summary["subject"].startswith("2 active alerts need attention")
    assert "WHAT TO DO FIRST" in summary["body_text"]
    assert "Next action:" in summary["body_text"]
    assert "Active alerts" in summary["body_html"]
    assert "Reports affected" in summary["body_html"]
    assert "Revenue &lt;Model&gt;" in summary["body_html"]
    assert "Revenue <Model>" not in summary["body_html"]
    assert summary["alerts"][0]["email_priority"] == "urgent"
    assert summary["mailto"].startswith("mailto:owner%40example.test")


def test_owner_schedule_content_is_alert_only():
    assert _normalize_content_types(["tasks", "alerts"]) == ["alerts"]
