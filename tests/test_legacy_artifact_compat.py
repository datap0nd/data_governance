"""Compatibility contract for the removed legacy artifact types.

Scripts, Windows Scheduled Tasks, and Power Automate flows are no longer
tracked artifact types (Flows replaced them). Their pages, APIs, and
scanners are gone, but existing database rows must stay intact and keep
resolving to friendly names, while every write path that could create new
legacy artifacts or links stays closed.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database
from app import flow_worker
from app.asset_visibility import get_active_source_ids
from app.models import (
    ActionUpdate,
    DocEntityLinkRequest,
    DocumentationCreate,
    DocumentationUpdate,
    TaskCreate,
    TaskLinkRequest,
    TaskUpdate,
)
from app.routers import actions, archive, documentation, tasks

LEGACY_TABLES = ("scripts", "script_tables", "scheduled_tasks", "power_automate_flows")


@pytest.fixture
def legacy_db(tmp_path):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    return database.DB_PATH


def _request(actor="Test User"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def test_existing_asap_csv_keeps_normalized_primary_and_byte_exact_raw_sibling(tmp_path):
    raw = (
        b"Weekly Sales Report\r\n\r\n"
        b"Filter,Value\r\nRegion,MENA\r\n\r\n"
        b"Week,Region,Units\r\n2026-W30,MENA,7\r\n2026-W31,MENA,9\r\n"
    )
    staged = tmp_path / "portal.csv"
    staged.write_bytes(raw)

    metadata = flow_worker._store_completed_download(
        staged, tmp_path / "weekly.csv", file_format="csv",
        asap_download_type="csv_file_format",
    )

    primary = tmp_path / "weekly.csv"
    sibling = tmp_path / "weekly_raw.csv"
    assert metadata["file_path"] == str(primary)
    assert metadata["original_file_path"] == str(sibling)
    assert sibling.read_bytes() == raw
    assert primary.read_text(encoding="utf-8-sig").splitlines() == [
        "Week,Region,Units", "2026-W30,MENA,7", "2026-W31,MENA,9",
    ]


def test_asap_csv_never_promotes_a_long_two_column_filter_block_to_header(tmp_path):
    staged = tmp_path / "portal.csv"
    staged.write_text(
        "Report title\n\n"
        "Filter,Value\nRegion,MENA\nProduct,Mobile\nChannel,Retail\nCurrency,USD\n\n"
        "Week,Units\n2026-W30,7\n",
        encoding="utf-8",
    )
    metadata = flow_worker._store_completed_download(
        staged, tmp_path / "weekly.csv", file_format="csv",
        asap_download_type="csv_file_format",
    )
    assert metadata["columns"] == ["Week", "Units"]
    assert (tmp_path / "weekly.csv").read_text(encoding="utf-8-sig").splitlines() == [
        "Week,Units", "2026-W30,7",
    ]


def _seed_legacy_rows(db):
    db.execute(
        "INSERT INTO scripts (id, path, display_name) VALUES (1, 'C:/jobs/refresh.py', 'refresh.py')"
    )
    db.execute(
        """INSERT INTO scheduled_tasks (id, task_name, task_path, status, enabled, script_id, last_result, archived)
           VALUES (1, 'Nightly refresh', '\\Nightly refresh', 'Ready', 1, 1, '1', 0)"""
    )
    db.execute(
        "INSERT INTO power_automate_flows (id, name, status) VALUES (1, 'Old PA flow', 'active')"
    )


def _table_names(db):
    return {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }


def test_init_db_keeps_legacy_tables_and_rows(legacy_db):
    with database.get_db() as db:
        assert set(LEGACY_TABLES) <= _table_names(db)
        _seed_legacy_rows(db)
        db.execute(
            "INSERT INTO script_tables (script_id, table_name, direction) VALUES (1, 'sales', 'write')"
        )

    # Re-running startup schema/migrations must not drop or clear anything.
    database.init_db()
    with database.get_db() as db:
        assert set(LEGACY_TABLES) <= _table_names(db)
        assert db.execute("SELECT COUNT(*) AS c FROM scripts").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) AS c FROM script_tables").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) AS c FROM scheduled_tasks").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) AS c FROM power_automate_flows").fetchone()["c"] == 1


def test_legacy_failure_actions_keep_names_and_resolve_manually(legacy_db):
    with database.get_db() as db:
        _seed_legacy_rows(db)
        db.execute(
            "INSERT INTO actions (id, type, status, scheduled_task_id) VALUES (10, 'task_failed', 'open', 1)"
        )
        db.execute(
            "INSERT INTO actions (id, type, status, script_id) VALUES (11, 'script_failed', 'open', 1)"
        )

    listed = {a.id: a for a in actions.list_actions()}
    task_action = listed[10]
    script_action = listed[11]

    assert task_action.asset_type == "scheduled_task"
    assert task_action.asset_name == "Nightly refresh"
    assert script_action.asset_type == "script"
    assert script_action.asset_name == "refresh.py"
    # The dedicated pages are gone, so no CTA may navigate to them.
    for action in (task_action, script_action):
        assert action.triage_cta not in ("Open task", "Open script")

    resolved = actions.update_action(10, ActionUpdate(status="resolved"), _request())
    assert resolved.status == "resolved"
    with database.get_db() as db:
        row = db.execute("SELECT status, resolved_at FROM actions WHERE id = 10").fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None


def test_existing_legacy_links_resolve_friendly_names(legacy_db):
    with database.get_db() as db:
        _seed_legacy_rows(db)
        db.execute("INSERT INTO tasks (id, title) VALUES (1, 'Legacy cleanup')")
        db.execute(
            "INSERT INTO task_links (task_id, entity_type, entity_id) VALUES (1, 'script', 1)"
        )
        db.execute(
            "INSERT INTO task_links (task_id, entity_type, entity_id) VALUES (1, 'scheduled_task', 1)"
        )
        db.execute("INSERT INTO documentation (id, title) VALUES (1, 'Legacy doc')")
        db.execute(
            "INSERT INTO doc_entity_links (doc_id, entity_type, entity_id) VALUES (1, 'script', 1)"
        )

        task_links = {l.entity_type: l for l in tasks._get_links(db, 1)}
        assert task_links["script"].entity_name == "refresh.py"
        assert task_links["scheduled_task"].entity_name == "Nightly refresh"

        doc_links = documentation._resolve_links(db, 1)
        assert doc_links[0].entity_type == "script"
        assert doc_links[0].entity_name == "refresh.py"


def test_linkable_entities_exclude_legacy_types(legacy_db):
    with database.get_db() as db:
        _seed_legacy_rows(db)

    options = tasks.list_linkable_entities()
    assert "script" not in options
    assert "scheduled_task" not in options
    assert {"report", "source", "upstream_system"} <= set(options)

    doc_options = documentation.get_doc_options()
    assert "scripts" not in doc_options


def test_new_legacy_task_links_rejected_but_existing_round_trip(legacy_db):
    with database.get_db() as db:
        _seed_legacy_rows(db)
        db.execute("INSERT INTO sources (id, name, type) VALUES (5, 'Source Five', 'excel')")

    with pytest.raises(HTTPException) as exc:
        tasks.create_task(
            TaskCreate(title="New", linked_entities=[TaskLinkRequest(entity_type="script", entity_id=1)]),
            _request(),
        )
    assert exc.value.status_code == 400

    # A task that already carries a legacy link keeps it through an edit …
    with database.get_db() as db:
        db.execute("INSERT INTO tasks (id, title) VALUES (2, 'Existing')")
        db.execute(
            "INSERT INTO task_links (task_id, entity_type, entity_id) VALUES (2, 'script', 1)"
        )
    updated = tasks.update_task(
        2,
        TaskUpdate(linked_entities=[
            {"entity_type": "script", "entity_id": 1},
            {"entity_type": "source", "entity_id": 5},
        ]),
        _request(),
    )
    kept = {(l.entity_type, l.entity_id) for l in updated.linked_entities}
    assert ("script", 1) in kept
    assert ("source", 5) in kept

    # … cannot gain a NEW legacy link …
    with pytest.raises(HTTPException) as exc:
        tasks.update_task(
            2,
            TaskUpdate(linked_entities=[
                {"entity_type": "script", "entity_id": 1},
                {"entity_type": "scheduled_task", "entity_id": 1},
            ]),
            _request(),
        )
    assert exc.value.status_code == 400

    # … and may drop the legacy link entirely.
    cleared = tasks.update_task(2, TaskUpdate(linked_entities=[]), _request())
    assert cleared.linked_entities == []


def test_new_legacy_doc_links_rejected_but_existing_round_trip(legacy_db):
    with database.get_db() as db:
        _seed_legacy_rows(db)

    with pytest.raises(HTTPException) as exc:
        documentation.create_doc(
            DocumentationCreate(
                title="New doc",
                linked_entities=[DocEntityLinkRequest(entity_type="script", entity_id=1)],
            ),
            _request(),
        )
    assert exc.value.status_code == 400

    with database.get_db() as db:
        db.execute("INSERT INTO documentation (id, title) VALUES (3, 'Doc three')")
        db.execute(
            "INSERT INTO doc_entity_links (doc_id, entity_type, entity_id) VALUES (3, 'script', 1)"
        )
    updated = documentation.update_doc(
        3,
        DocumentationUpdate(linked_entities=[DocEntityLinkRequest(entity_type="script", entity_id=1)]),
        _request(),
    )
    assert {(l.entity_type, l.entity_id) for l in updated.linked_entities} == {("script", 1)}

    with pytest.raises(HTTPException) as exc:
        documentation.update_doc(
            3,
            DocumentationUpdate(linked_entities=[
                DocEntityLinkRequest(entity_type="script", entity_id=1),
                DocEntityLinkRequest(entity_type="scheduled_task", entity_id=1),
            ]),
            _request(),
        )
    assert exc.value.status_code == 400

    cleared = documentation.update_doc(3, DocumentationUpdate(linked_entities=[]), _request())
    assert cleared.linked_entities == []


@pytest.mark.parametrize("entity_type", ["script", "scheduled_task", "power_automate"])
def test_archive_rejects_legacy_entity_types(legacy_db, entity_type):
    with database.get_db() as db:
        _seed_legacy_rows(db)

    with pytest.raises(HTTPException) as exc:
        archive.toggle_archive(entity_type, 1, _request())
    assert exc.value.status_code == 400


def test_no_legacy_routes_registered():
    from app.main import app

    legacy_prefixes = ("/api/scripts", "/api/scheduled-tasks", "/api/power-automate-flows")
    offending = [
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith(legacy_prefixes)
    ]
    assert offending == []


def test_script_only_source_is_not_active(legacy_db):
    with database.get_db() as db:
        _seed_legacy_rows(db)
        db.execute(
            "INSERT INTO sources (id, name, type, discovered_by) VALUES (1, 'Script only', 'excel', 'scan')"
        )
        db.execute(
            "INSERT INTO sources (id, name, type, discovered_by) VALUES (2, 'Report linked', 'excel', 'scan')"
        )
        db.execute(
            "INSERT INTO script_tables (script_id, table_name, direction, source_id) VALUES (1, 'sales', 'write', 1)"
        )
        db.execute("INSERT INTO reports (id, name) VALUES (1, 'Report One')")
        db.execute(
            "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (1, 'sales', 2)"
        )

        active = get_active_source_ids(db)
        assert 1 not in active
        assert 2 in active
