from types import SimpleNamespace

import app.database as database
from app.database import get_db
from app.routers import reports


def test_report_refresh_lazily_resolves_and_persists_dataset_id(tmp_path, monkeypatch):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    with get_db() as db:
        db.execute("INSERT INTO reports (id, name) VALUES (1, 'Small Report')")

    calls = []
    monkeypatch.setattr(reports, "PBI_WORKSPACE", "Governed Workspace")
    monkeypatch.setattr(
        reports,
        "resolve_report_dataset",
        lambda workspace, name: {
            "workspace": {"id": "workspace-1", "name": workspace},
            "report_id": "report-1",
            "report_name": name,
            "dataset_id": "33333333-3333-3333-3333-333333333333",
            "web_url": "https://app.powerbi.test/reports/report-1",
        },
    )
    monkeypatch.setattr(
        reports,
        "trigger_dataset_refresh",
        lambda workspace, dataset_id: calls.append((workspace, dataset_id)) or {
            "status": "accepted",
            "request_id": "request-1",
        },
    )

    result = reports.refresh_report(1, SimpleNamespace(state=SimpleNamespace(actor="Tester")))

    assert result["status"] == "accepted"
    assert calls == [
        ("Governed Workspace", "33333333-3333-3333-3333-333333333333")
    ]
    with get_db() as db:
        saved = db.execute(
            "SELECT pbi_dataset_id, powerbi_url FROM reports WHERE id=1"
        ).fetchone()
        event = db.execute(
            "SELECT action, actor FROM event_log WHERE entity_type='report' AND entity_id=1"
        ).fetchone()
    assert saved["pbi_dataset_id"] == "33333333-3333-3333-3333-333333333333"
    assert saved["powerbi_url"] == "https://app.powerbi.test/reports/report-1"
    assert event["action"] == "refresh_requested"
    assert event["actor"] == "Tester"
