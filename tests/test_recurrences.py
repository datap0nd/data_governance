import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app import database, pbi_visual_export
from app.routers import recurrences


@pytest.fixture(autouse=True)
def _successful_refresh(monkeypatch):
    monkeypatch.setattr(
        recurrences.pbi_fetch,
        "fetch_dataset_last_refresh",
        lambda workspace_id, dataset_id: {
            "status": "Completed",
            "start_time": "2026-07-16T07:55:00Z",
            "end_time": "2026-07-16T08:00:00Z",
            "error": None,
        },
    )


def _valid_write(**overrides):
    data = {
        "name": "Weekly exceptions",
        "enabled": True,
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "workspace_name": "Analytics",
        "report_id": "22222222-2222-2222-2222-222222222222",
        "report_name": "Operations",
        "report_url": "https://app.powerbi.com/groups/111/reports/222",
        "embed_url": "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
        "dataset_id": "33333333-3333-3333-3333-333333333333",
        "page_name": "ReportSection123",
        "page_display_name": "Summary",
        "visual_name": "visual456",
        "visual_title": "Exceptions",
        "visual_type": "tableEx",
        "group_column": "Subsidiary",
        "subject_template": "{recurrence} - {group}",
        "recurrence": "weekly",
        "weekdays": ["monday"],
        "send_time": "08:00",
        "groups": [
            {
                "match_value": "North",
                "display_name": "North team",
                "recipients": "north@example.com",
                "enabled": True,
            }
        ],
        "rules": [{"column_name": "Value", "operator": "gt", "compare_value": "10"}],
    }
    data.update(overrides)
    return recurrences.RecurrenceWrite(**data)


def _seed_owner_data():
    with database.get_db() as db:
        db.execute(
            "INSERT INTO people (name, role, email) VALUES (?, ?, ?)",
            ("Report Owner", "BI", "owner@example.com"),
        )
        db.execute(
            """INSERT INTO reports (name, owner, pbi_dataset_id)
               VALUES (?, ?, ?)""",
            (
                "Operations",
                "Report Owner",
                "33333333-3333-3333-3333-333333333333",
            ),
        )


def _seed_recurrence(db_path):
    database.DB_PATH = str(db_path)
    database.init_db()
    _seed_owner_data()
    with database.get_db() as db:
        cursor = db.execute(
            """INSERT INTO pbi_recurrences
               (name, enabled, workspace_id, workspace_name, report_id, report_name,
                report_url, embed_url, dataset_id, page_name, page_display_name,
                visual_name, visual_title, visual_type, owner_name, group_column, subject_template,
                recurrence, weekdays, send_time, next_run_at)
               VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'weekly', 'monday', '08:00', ?)""",
            (
                "Weekly exceptions",
                "11111111-1111-1111-1111-111111111111",
                "Analytics",
                "22222222-2222-2222-2222-222222222222",
                "Operations",
                "https://app.powerbi.com/groups/111/reports/222",
                "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
                "33333333-3333-3333-3333-333333333333",
                "ReportSection123",
                "Summary",
                "visual456",
                "Exceptions",
                "tableEx",
                "Report Owner",
                "Subsidiary",
                "{recurrence} - {group} ({row_count})",
                "2099-01-05T08:00:00",
            ),
        )
        recurrence_id = cursor.lastrowid
        db.executemany(
            """INSERT INTO pbi_recurrence_groups
               (recurrence_id, match_value, display_name, recipients, enabled)
               VALUES (?, ?, ?, ?, 1)""",
            [
                (recurrence_id, "North", "North team", "north@example.com"),
                (recurrence_id, "South", "South team", "south@example.com"),
            ],
        )
        db.execute(
            """INSERT INTO pbi_recurrence_rules
               (recurrence_id, position, column_name, operator, compare_value)
               VALUES (?, 0, 'Value', 'gt', '10')""",
            (recurrence_id,),
        )
    return recurrence_id


def test_parse_visual_csv_preserves_dynamic_columns_and_deduplicates_headers():
    columns, rows = recurrences.parse_visual_csv(
        "\ufeffSubsidiary,Value,Value,New Column\nNorth,10,11,Owner added\n"
    )
    assert columns == ["Subsidiary", "Value", "Value (2)", "New Column"]
    assert rows == [
        {
            "Subsidiary": "North",
            "Value": "10",
            "Value (2)": "11",
            "New Column": "Owner added",
        }
    ]


@pytest.mark.parametrize(
    ("actual", "expected", "operator", "result"),
    [
        ("1,234.50", "1000", "gt", True),
        ("12.5%", "10", "gt", True),
        ("9", "10", "gte", False),
        ("North Region", "north", "contains", True),
        ("", "", "is_empty", True),
    ],
)
def test_rule_matches_numeric_and_text_values(actual, expected, operator, result):
    rule = {"column_name": "Metric", "operator": operator, "compare_value": expected}
    assert recurrences.rule_matches({"Metric": actual}, rule) is result


def test_rules_are_combined_with_and():
    rows = [
        {"Subsidiary": "North", "Value": "20"},
        {"Subsidiary": "South", "Value": "20"},
        {"Subsidiary": "North", "Value": "5"},
    ]
    rules = [
        {"column_name": "Subsidiary", "operator": "eq", "compare_value": "North"},
        {"column_name": "Value", "operator": "gt", "compare_value": "10"},
    ]
    assert recurrences.apply_rules(rows, rules) == [{"Subsidiary": "North", "Value": "20"}]


def test_next_run_weekly_and_monthly_clamping():
    monday_after = recurrences.calculate_next_run(
        "weekly", "08:00", ["monday"], None, datetime(2026, 7, 13, 9, 0)
    )
    assert monday_after == datetime(2026, 7, 20, 8, 0)
    february = recurrences.calculate_next_run(
        "monthly", "08:00", [], 31, datetime(2026, 2, 1, 9, 0)
    )
    assert february == datetime(2026, 2, 28, 8, 0)


def test_configuration_rejects_non_powerbi_embed_url_and_non_table_visual():
    with pytest.raises(ValueError, match="app.powerbi.com"):
        _valid_write(embed_url="https://example.com/reportEmbed")
    with pytest.raises(ValueError, match="HTTPS app.powerbi.com"):
        _valid_write(embed_url="http://app.powerbi.com/reportEmbed")
    with pytest.raises(ValueError, match="report URL"):
        _valid_write(report_url="https://example.com/report")
    with pytest.raises(ValueError, match="table or matrix"):
        _valid_write(visual_type="lineChart")


def test_configuration_allows_single_email_without_subgroups_or_rules():
    body = _valid_write(
        delivery_mode="single",
        recipients="team@example.com",
        group_column="",
        groups=[],
        rules=[],
    )

    assert body.delivery_mode == "single"
    assert body.recipients == "team@example.com"
    assert body.groups == []


def test_disabled_subgroup_does_not_require_recipients():
    body = _valid_write(groups=[
        {
            "match_value": "North",
            "display_name": "North team",
            "recipients": "north@example.com",
            "enabled": True,
        },
        {
            "match_value": "South",
            "display_name": "",
            "recipients": "",
            "enabled": False,
        },
    ])

    assert body.groups[1].display_name == ""
    assert body.groups[1].recipients == ""


def test_single_email_configuration_persists_without_subgroups(tmp_path, monkeypatch):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    _seed_owner_data()
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    request = SimpleNamespace(state=SimpleNamespace(actor="Test User"))
    body = _valid_write(
        delivery_mode="single",
        recipients="team@example.com",
        group_column="",
        groups=[],
        rules=[],
    )

    created = recurrences.create_recurrence(body, request)
    body.name = "Updated single email"
    updated = recurrences.update_recurrence(created["id"], body, request)

    assert created["delivery_mode"] == "single"
    assert created["groups"] == []
    assert created["owner_name"] == "Report Owner"
    assert updated["name"] == "Updated single email"
    assert updated["recipients"] == "team@example.com"


def test_report_picker_includes_default_owner_and_people_email(tmp_path, monkeypatch):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    _seed_owner_data()
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences.pbi_fetch,
        "fetch_workspace_reports",
        lambda: {
            "workspace": {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Analytics",
            },
            "reports": [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "name": "Operations",
                    "dataset_id": "33333333-3333-3333-3333-333333333333",
                    "web_url": "https://app.powerbi.com/groups/111/reports/222",
                    "embed_url": "https://app.powerbi.com/reportEmbed?reportId=222",
                }
            ],
        },
    )

    payload = recurrences.recurrence_reports()

    assert payload["reports"][0]["owner_name"] == "Report Owner"
    assert payload["reports"][0]["owner_email"] == "owner@example.com"
    assert len(payload["people"]) == 1
    assert payload["people"][0]["name"] == "Report Owner"
    assert payload["people"][0]["role"] == "BI"
    assert payload["people"][0]["email"] == "owner@example.com"


def test_missing_recurrence_does_not_leave_run_lock(tmp_path, monkeypatch):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    monkeypatch.setattr(recurrences, "get_db", database.get_db)

    with pytest.raises(KeyError, match="Recurrence not found"):
        recurrences.run_recurrence(999, mode="send", trigger_type="manual")

    assert 999 not in recurrences._RUNNING_IDS


def test_power_bi_client_loader_falls_back_to_second_package_cdn():
    class FakePage:
        def __init__(self):
            self.urls = []
            self.timeouts = []

        def set_default_timeout(self, value):
            self.timeouts.append(value)

        def add_script_tag(self, *, url):
            self.urls.append(url)
            if len(self.urls) == 1:
                raise RuntimeError("DNS lookup failed")

        def evaluate(self, expression):
            return True

    page = FakePage()
    selected = pbi_visual_export._load_power_bi_client(page)

    assert page.urls == list(pbi_visual_export.POWER_BI_CLIENT_URLS)
    assert selected == pbi_visual_export.POWER_BI_CLIENT_URLS[1]
    assert page.timeouts == [20000]


def test_visual_export_uses_phased_embedding_and_hides_unselected_visuals():
    runtime = pbi_visual_export._RUNTIME_HTML

    assert "window.powerbi.load" in runtime
    assert 'report.on("loaded"' in runtime
    assert "setVisualDisplayState" in runtime
    assert "VisualContainerDisplayMode.Hidden" in runtime
    assert "window.powerbi.embed" not in runtime


def test_visual_export_retries_once_when_edge_closes_during_startup(monkeypatch):
    class FakePlaywrightError(RuntimeError):
        pass

    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    attempts = []

    def run_attempt(playwright, config):
        attempts.append((playwright, config))
        if len(attempts) == 1:
            raise FakePlaywrightError(
                "BrowserContext.new_page: Target page, context or browser has been closed"
            )
        return {"data": "Column\nValue\n"}

    monkeypatch.setattr(pbi_visual_export, "_run_browser_attempt", run_attempt)

    result = pbi_visual_export._run_browser_with_retry(
        playwright_factory=FakePlaywrightContext,
        playwright_error_types=(FakePlaywrightError,),
        config={"action": "export"},
    )

    assert result == {"data": "Column\nValue\n"}
    assert len(attempts) == 2


def test_visual_export_reports_repeated_edge_close_after_retry(monkeypatch):
    class FakePlaywrightError(RuntimeError):
        pass

    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    attempts = []

    def run_attempt(playwright, config):
        attempts.append((playwright, config))
        raise FakePlaywrightError(
            "BrowserContext.new_page: Target page, context or browser has been closed"
        )

    monkeypatch.setattr(pbi_visual_export, "_run_browser_attempt", run_attempt)

    with pytest.raises(
        pbi_visual_export.PbiVisualExportError,
        match="including one retry with a fresh browser",
    ):
        pbi_visual_export._run_browser_with_retry(
            playwright_factory=FakePlaywrightContext,
            playwright_error_types=(FakePlaywrightError,),
            config={"action": "export"},
        )

    assert len(attempts) == 2


def test_run_sends_one_html_message_per_matching_subgroup(tmp_path, monkeypatch):
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences,
        "export_visual_data",
        lambda *args, **kwargs: {
            "data": (
                "Subsidiary,Value,New Column\n"
                "North,5,below threshold\n"
                "North,15,owner added this\n"
                "South,20,owner added this too\n"
            ),
            "visual": {"name": "visual456", "type": "tableEx", "title": "Exceptions"},
        },
    )
    launched = []
    monkeypatch.setattr(
        recurrences,
        "_launch_outlook_payload",
        lambda messages, mode: launched.append((messages, mode)) or len(messages),
    )

    result = recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="manual")

    assert result["status"] == "sent"
    assert result["exported_rows"] == 3
    assert result["matched_rows"] == 2
    assert result["email_count"] == 2
    assert len(launched) == 1
    messages, mode = launched[0]
    assert mode == "send"
    assert {message["to"] for message in messages} == {"north@example.com", "south@example.com"}
    assert all("New Column" in message["html_body"] for message in messages)
    assert all("METRONOME ALERT" in message["html_body"] for message in messages)
    assert all("Alert results" in message["html_body"] for message in messages)
    assert all("background:#0d7377" in message["html_body"] for message in messages)
    assert all("Open the Power BI report" in message["html_body"] for message in messages)
    assert "below threshold" not in "".join(message["html_body"] for message in messages)


def test_run_sends_one_email_for_all_filtered_rows_without_subgroups(tmp_path, monkeypatch):
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    with database.get_db() as db:
        db.execute(
            "UPDATE pbi_recurrences SET delivery_mode = 'single', recipients = ?, group_column = '' WHERE id = ?",
            ("team@example.com", recurrence_id),
        )
        db.execute("DELETE FROM pbi_recurrence_groups WHERE recurrence_id = ?", (recurrence_id,))
    monkeypatch.setattr(
        recurrences,
        "export_visual_data",
        lambda *args, **kwargs: {
            "data": "Subsidiary,Value\nNorth,5\nNorth,15\nSouth,20\n",
            "visual": {"name": "visual456", "type": "tableEx", "title": "Exceptions"},
        },
    )
    launched = []
    monkeypatch.setattr(
        recurrences,
        "_launch_outlook_payload",
        lambda messages, mode: launched.append((messages, mode)) or len(messages),
    )

    result = recurrences.run_recurrence(recurrence_id, mode="draft", trigger_type="manual")

    assert result["status"] == "drafted"
    assert result["matched_rows"] == 2
    assert result["email_count"] == 1
    assert launched[0][0][0]["to"] == "team@example.com"
    assert "North" in launched[0][0][0]["html_body"]
    assert "South" in launched[0][0][0]["html_body"]


def test_run_fails_closed_when_required_column_disappears(tmp_path, monkeypatch):
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences,
        "export_visual_data",
        lambda *args, **kwargs: {
            "data": "Subsidiary,Other\nNorth,20\n",
            "visual": {"name": "visual456", "type": "tableEx", "title": "Exceptions"},
        },
    )
    launched = []
    monkeypatch.setattr(
        recurrences,
        "_launch_outlook_payload",
        lambda messages, mode: launched.append((messages, mode)) or len(messages),
    )

    with pytest.raises(RuntimeError, match="required columns are missing: Value"):
        recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="manual")

    assert len(launched) == 1
    messages, mode = launched[0]
    assert mode == "send"
    assert len(messages) == 1
    assert messages[0]["to"] == "owner@example.com"
    assert "Metronome alert failed" in messages[0]["subject"]
    assert "Value" in messages[0]["html_body"]
    with database.get_db() as db:
        row = db.execute(
            "SELECT last_status, last_error FROM pbi_recurrences WHERE id = ?", (recurrence_id,)
        ).fetchone()
    assert row["last_status"] == "failed"
    assert "Value" in row["last_error"]


def test_failed_latest_refresh_blocks_export_and_notifies_owner(tmp_path, monkeypatch):
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences.pbi_fetch,
        "fetch_dataset_last_refresh",
        lambda workspace_id, dataset_id: {
            "status": "Failed",
            "start_time": "2026-07-16T07:55:00Z",
            "end_time": "2026-07-16T08:00:00Z",
            "error": "Gateway could not reach the data source.",
        },
    )
    monkeypatch.setattr(
        recurrences,
        "export_visual_data",
        lambda *args, **kwargs: pytest.fail("Visual export must not run after a failed refresh"),
    )
    launched = []
    monkeypatch.setattr(
        recurrences,
        "_launch_outlook_payload",
        lambda messages, mode: launched.append((messages, mode)) or len(messages),
    )

    with pytest.raises(recurrences.RecurrenceRefreshError, match="latest refresh did not succeed"):
        recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="manual")

    assert len(launched) == 1
    messages, mode = launched[0]
    assert mode == "send"
    assert messages[0]["to"] == "owner@example.com"
    assert "Latest refresh" in messages[0]["html_body"]
    assert "Failed" in messages[0]["html_body"]
    assert "Gateway could not reach the data source" in messages[0]["html_body"]
    assert "DELIVERY BLOCKED" in messages[0]["html_body"]
    assert "What needs attention" in messages[0]["html_body"]
    assert "background:#8f2d2d" in messages[0]["html_body"]
    assert "Open the Power BI report" in messages[0]["html_body"]
    with database.get_db() as db:
        run = db.execute(
            """SELECT status, detail, error
               FROM pbi_recurrence_runs
               WHERE recurrence_id = ?
               ORDER BY id DESC
               LIMIT 1""",
            (recurrence_id,),
        ).fetchone()
    detail = json.loads(run["detail"])
    assert run["status"] == "failed"
    assert "latest refresh did not succeed" in run["error"]
    assert detail["refresh"]["status"] == "Failed"
    assert detail["failure_notification"]["status"] == "launched"


def test_draft_failure_does_not_email_owner(tmp_path, monkeypatch):
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences.pbi_fetch,
        "fetch_dataset_last_refresh",
        lambda workspace_id, dataset_id: {
            "status": "InProgress",
            "start_time": "2026-07-16T07:55:00Z",
            "end_time": None,
            "error": None,
        },
    )
    monkeypatch.setattr(
        recurrences,
        "_launch_outlook_payload",
        lambda *args, **kwargs: pytest.fail("Draft failures must not email the owner"),
    )

    with pytest.raises(recurrences.RecurrenceRefreshError, match="InProgress"):
        recurrences.run_recurrence(recurrence_id, mode="draft", trigger_type="manual")

    with database.get_db() as db:
        run = db.execute(
            """SELECT detail
               FROM pbi_recurrence_runs
               WHERE recurrence_id = ?
               ORDER BY id DESC
               LIMIT 1""",
            (recurrence_id,),
        ).fetchone()
    detail = json.loads(run["detail"])
    assert detail["failure_notification"]["status"] == "not_applicable"
