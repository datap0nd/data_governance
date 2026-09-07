"""Retiring TMDL Checker keeps metadata scanning and historical evidence intact."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.checks import best_practices as legacy_checker
from app.routers import actions, documentation, email, scanner, schedules
from app.scanner import modules, runner
from test_scan_lifecycle import _stub_scan_components


@pytest.fixture
def checker_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'checker-retirement.db'))
    database.init_db()
    # A retired API or scan must never execute the old report checker.
    monkeypatch.setattr(legacy_checker, 'scan_all', lambda *args, **kwargs:
                        pytest.fail('Retired TMDL Checker executed'))
    with database.get_db() as db:
        db.execute("""INSERT INTO actions (id,type,status,fingerprint,notes,created_at,updated_at)
                      VALUES (90,'best_practice','resolved','best_practice:old',
                              'Historical checker finding','2026-01-01','2026-01-02')""")
        db.execute("""INSERT INTO scan_runs(id,started_at,finished_at,status,components_json)
                      VALUES (90,'2026-01-01','2026-01-01','completed',?)""",
                   (json.dumps({'governance': {'status': 'completed', 'best_practices': {'total': 2}}}),))
    return _history()


def _history():
    with database.get_db() as db:
        return {
            'actions': [dict(row) for row in db.execute("SELECT * FROM actions WHERE type='best_practice'")],
            'old_scan': dict(db.execute('SELECT * FROM scan_runs WHERE id=90').fetchone()),
        }


def test_checker_endpoint_returns_gone_without_scanning_or_changing_history(checker_db):
    client = TestClient(main.app, base_url='http://127.0.0.1')
    response = client.get('/api/best-practices')
    assert response.status_code == 410
    assert 'TMDL Checker has been retired' in response.json()['detail']
    assert 'catalog discovery and lineage remain available' in response.json()['detail']
    assert _history() == checker_db
    paths = main.app.openapi()['paths']
    assert '/api/best-practices' not in paths
    assert any(path.startswith('/api/data-quality') for path in paths)
    assert any(path.startswith('/api/scanner') for path in paths)
    assert any(path.startswith('/api/lineage') for path in paths)


@pytest.mark.parametrize('schedule_fails', [False, True])
def test_standalone_governance_keeps_remaining_checks_and_failure_isolation(checker_db, monkeypatch, schedule_fails):
    calls = []

    def schedule_scan(*, persist):
        assert persist is True
        calls.append('schedules')
        if schedule_fails:
            raise RuntimeError('synthetic schedule check failure')
        return {'status': 'completed'}

    def document_scan():
        calls.append('documentation')
        return {'status': 'completed'}

    monkeypatch.setattr(schedules, 'run_schedule_discrepancy_scan', schedule_scan)
    monkeypatch.setattr(documentation, 'sync_documentation_completeness_actions', document_scan)
    result = scanner._run_governance_subscans()
    assert calls == ['schedules', 'documentation']
    assert set(result) == {'status', 'failed_subscans', 'schedule_discrepancies', 'documentation'}
    assert result['status'] == ('failed' if schedule_fails else 'completed')
    assert result['failed_subscans'] == (['schedule_discrepancies'] if schedule_fails else [])
    assert result['documentation']['status'] == 'completed'
    assert _history() == checker_db


def test_full_scan_omits_checker_but_keeps_governance_and_history(checker_db, monkeypatch):
    observations = _stub_scan_components(monkeypatch, {'status': 'completed'})
    result = runner.run_scan('unused', run_followup_probe=False)
    assert result['status'] == 'completed'
    governance = result['components']['governance']
    assert governance['status'] == 'completed'
    assert 'best_practices' not in governance
    assert governance['schedule_discrepancies']['status'] == 'completed'
    assert governance['documentation']['status'] == 'completed'
    observed = {name for name, _, _ in observations}
    assert {'schedule_discrepancies', 'documentation'} <= observed
    assert 'best_practices' not in observed
    assert _history() == checker_db
    with database.get_db() as db:
        stored = db.execute("SELECT details_json FROM scanner_module_runs WHERE module_key='governance' ORDER BY id DESC LIMIT 1").fetchone()
    assert 'best_practices' not in json.loads(stored['details_json'])


def test_scanner_catalog_still_describes_pbix_tmdl_discovery():
    catalog = modules.MODULES_BY_KEY['report_catalog']
    assert 'PBIX' in catalog['description'] and 'TMDL' in catalog['description']
    assert 'tables, measures, visuals, and source expressions' in catalog['description']
    assert 'best-practice' not in modules.MODULES_BY_KEY['governance']['description']


def test_even_open_legacy_checker_findings_stay_out_of_alert_emails(checker_db):
    with database.get_db() as db:
        db.execute("INSERT INTO reports(id,name,owner,archived) VALUES (91,'Historical report','Retired Owner',0)")
        db.execute("INSERT INTO people(id,name,role,email,include_all_alerts) VALUES (91,'Retired Owner','BI','owner@example.test',1)")
        db.execute("UPDATE actions SET status='open',report_id=91,assigned_to='Retired Owner' WHERE id=90")
    before = _history()
    assert actions.list_actions(status='open') == []
    assert email._load_alert_summaries(ai_settings=SimpleNamespace(feature_enabled=lambda feature: False)) == []
    assert _history() == before
