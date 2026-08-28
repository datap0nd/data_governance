import json

import pytest

from app import database
from app.checks.data_quality import CheckDefinitionError, run_quality_checks, validate_config
from app.scanner.findings import sync_managed_actions


@pytest.fixture
def quality_db(tmp_path):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO sources
               (id, name, type, owner, discovered_by, archived)
               VALUES (1, 'Orders', 'excel', 'Data Owner', 'manual', 0)"""
        )
    return database.DB_PATH


def _add_check(db, check_type, config, name="Orders check"):
    cursor = db.execute(
        """INSERT INTO checks
           (name, source_id, type, config, severity, enabled)
           VALUES (?, 1, ?, ?, 'critical', 1)""",
        (name, check_type, json.dumps(config)),
    )
    return cursor.lastrowid


def _probe(db, row_count, status="fresh"):
    db.execute(
        """INSERT INTO source_probes
           (source_id, probed_at, row_count, status, message)
           VALUES (1, CURRENT_TIMESTAMP, ?, ?, 'test probe')""",
        (row_count, status),
    )


def test_check_config_rejects_invalid_limits():
    with pytest.raises(CheckDefinitionError):
        validate_config("row_count_range", {"min_rows": 20, "max_rows": 10})
    with pytest.raises(CheckDefinitionError):
        validate_config("null_rate", {"column": "value", "max_null_pct": 101})


def test_failed_probe_check_creates_owned_incident_and_pass_resolves_it(quality_db):
    with database.get_db() as db:
        check_id = _add_check(db, "row_count_range", {"min_rows": 100, "max_rows": None})
        _probe(db, 50)

    failed = run_quality_checks([check_id])

    assert failed["fail"] == 1
    assert failed["actions_created"] == 1
    with database.get_db() as db:
        action = db.execute(
            "SELECT * FROM actions WHERE check_id = ? AND type = 'data_quality'",
            (check_id,),
        ).fetchone()
        alert = db.execute(
            "SELECT * FROM alerts WHERE check_result_id IS NOT NULL"
        ).fetchone()
        assert action["status"] == "open"
        assert action["assigned_to"] == "Data Owner"
        assert action["fingerprint"] == f"data_quality:{check_id}"
        assert alert["assigned_to"] == "Data Owner"

    with database.get_db() as db:
        _probe(db, 150)

    passed = run_quality_checks([check_id])

    assert passed["pass"] == 1
    assert passed["actions_resolved"] == 1
    with database.get_db() as db:
        action = db.execute(
            "SELECT * FROM actions WHERE check_id = ? ORDER BY id DESC LIMIT 1",
            (check_id,),
        ).fetchone()
        alert = db.execute(
            "SELECT * FROM alerts WHERE check_result_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert action["status"] == "resolved"
        assert alert["resolution_status"] == "resolved"


def test_row_count_change_uses_two_latest_probes(quality_db):
    with database.get_db() as db:
        check_id = _add_check(
            db,
            "row_count_change",
            {"max_drop_pct": 20, "max_increase_pct": None},
        )
        _probe(db, 100)
        _probe(db, 70)

    result = run_quality_checks([check_id])

    assert result["fail"] == 1
    assert result["results"][0]["value"] == -30.0


def test_managed_finding_updates_resolves_and_reopens(quality_db):
    finding = {
        "fingerprint": "broken_ref:12",
        "report_id": None,
        "source_id": 1,
        "assigned_to": "Data Owner",
        "notes": "first observation",
    }
    with database.get_db() as db:
        first = sync_managed_actions(db, "broken_ref", [finding], "2026-01-01T00:00:00+00:00")
        finding["notes"] = "updated observation"
        second = sync_managed_actions(db, "broken_ref", [finding], "2026-01-02T00:00:00+00:00")
        cleared = sync_managed_actions(db, "broken_ref", [], "2026-01-03T00:00:00+00:00")
        reopened = sync_managed_actions(db, "broken_ref", [finding], "2026-01-04T00:00:00+00:00")
        rows = db.execute(
            "SELECT status, notes FROM actions WHERE fingerprint = 'broken_ref:12' ORDER BY id"
        ).fetchall()

    assert first["created"] == 1
    assert second["updated"] == 1
    assert cleared["resolved"] == 1
    assert reopened["created"] == 1
    assert [row["status"] for row in rows] == ["resolved", "open"]
    assert rows[-1]["notes"] == "updated observation"


def test_postgres_checks_route_by_exact_endpoint_database_and_relation(quality_db, monkeypatch):
    from app.checks import data_quality

    exact_sources = [
        (10, "Primary warehouse orders", "primary.internal", "warehouse"),
        (11, "Primary staging orders", "primary.internal", "staging"),
        (12, "Flow orders", "flow.internal", "flow_db"),
        (13, "Unconfigured orders", "other.internal", "other_db"),
    ]
    check_ids = []
    with database.get_db() as db:
        for source_id, name, server, database_name in exact_sources:
            db.execute(
                """INSERT INTO sources
                   (id, name, type, connection_info, owner, discovered_by, archived)
                   VALUES (?, ?, 'postgresql', 'misleading/default/wrong.relation',
                           'Data Owner', 'scanner', 0)""",
                (source_id, name),
            )
            db.execute(
                """INSERT INTO source_postgres_identities
                   (source_id, server_name, database_name, schema_name, relation_name, relation_kind)
                   VALUES (?, ?, ?, 'sales', 'orders', 'table')""",
                (source_id, server, database_name),
            )
            cursor = db.execute(
                """INSERT INTO checks
                   (name, source_id, type, config, severity, enabled)
                   VALUES (?, ?, 'null_rate', ?, 'critical', 1)""",
                (f"Check {source_id}", source_id, json.dumps({"column": "id", "max_null_pct": 0})),
            )
            check_ids.append(cursor.lastrowid)

        # A PostgreSQL source without structured coordinates must also fail
        # closed instead of borrowing the configured default database.
        db.execute(
            """INSERT INTO sources
               (id, name, type, connection_info, owner, discovered_by, archived)
               VALUES (14, 'Identity-less orders', 'postgresql',
                       'primary.internal/warehouse/sales.orders',
                       'Data Owner', 'scanner', 0)"""
        )
        cursor = db.execute(
            """INSERT INTO checks
               (name, source_id, type, config, severity, enabled)
               VALUES ('Check 14', 14, 'null_rate', ?, 'critical', 1)""",
            (json.dumps({"column": "id", "max_null_pct": 0}),),
        )
        check_ids.append(cursor.lastrowid)

    class FakeConnection:
        def __init__(self, profile, database_name):
            self.profile = profile
            self.database_name = database_name
            self.closed = False

        def close(self):
            self.closed = True

    connection_calls = []
    opened = []

    def primary_connection(database=None):
        connection_calls.append(("primary", database))
        connection = FakeConnection("primary", database)
        opened.append(connection)
        return connection

    def flow_connection(database=None):
        connection_calls.append(("flow", database))
        connection = FakeConnection("flow", database)
        opened.append(connection)
        return connection

    executions = []

    def exact_check(connection, schema, relation, check_type, config):
        executions.append(
            (connection.profile, connection.database_name, schema, relation, check_type, config["column"])
        )
        return "pass", 0.0, "Exact relation passed"

    monkeypatch.setattr(data_quality, "PGHOST", "primary.internal")
    monkeypatch.setattr(data_quality, "PGPORT", "5432")
    monkeypatch.setattr(data_quality, "UPLOAD_PGHOST", "flow.internal")
    monkeypatch.setattr(data_quality, "UPLOAD_PGPORT", "5432")
    monkeypatch.setattr(data_quality, "_get_pg_connection", primary_connection)
    monkeypatch.setattr(data_quality, "_get_flow_pg_connection", flow_connection)
    monkeypatch.setattr(data_quality, "_run_postgres_check", exact_check)

    result = data_quality.run_quality_checks(check_ids)

    assert connection_calls == [
        ("primary", "warehouse"),
        ("primary", "staging"),
        ("flow", "flow_db"),
    ]
    assert executions == [
        ("primary", "warehouse", "sales", "orders", "null_rate", "id"),
        ("primary", "staging", "sales", "orders", "null_rate", "id"),
        ("flow", "flow_db", "sales", "orders", "null_rate", "id"),
    ]
    assert result["pass"] == 3
    assert result["error"] == 2
    errors = {item["check_id"]: item["message"] for item in result["results"] if item["status"] == "error"}
    assert "not configured" in errors[check_ids[3]]
    assert "identity is unavailable" in errors[check_ids[4]]
    assert all(connection.closed for connection in opened)
