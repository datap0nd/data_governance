"""Reusable recordings: template listing, preview, copy and replicate behaviour."""
import copy
import json

import pytest
from fastapi import HTTPException

from app import database, flow_recording
from app.routers import flows, flow_recordings as routes
from test_flows import flow_db, _request
from test_flow_recordings import definition, draft_job


def _second_flow(site_id, name='Second flow'):
    return routes.create_draft(routes.RecordingDraft(name=name, site_id=site_id, report_url='http://localhost/other'), _request())


def _save(flow_id, value):
    return routes.save_revision(flow_id, routes.RevisionWrite(definition=value))['revision_id']


def test_templates_list_only_same_module_flows_with_recordings_and_default_to_active(flow_db):
    source, job = draft_job()
    site_id = job['site']['id']
    first = _save(source['id'], definition())
    changed = definition(); changed['steps'][0]['label'] = 'Open orders'
    second = _save(source['id'], changed)
    with database.get_db() as db:
        db.execute('UPDATE flows SET recording_revision_id=? WHERE id=?', (first, source['id']))
    destination = _second_flow(site_id)
    empty = _second_flow(site_id, 'No recording yet')
    gscm_site = flows.create_site(flows.SiteWrite(name='GSCM templates', adapter='gscm_portal', base_url='http://localhost/gscm', auth_url='http://localhost/gscm'), _request())
    gscm = _second_flow(gscm_site['id'], 'GSCM flow')
    _save(gscm['id'], definition())
    result = routes.list_templates(destination['id'])
    assert result['module'] == 'asap_portal' and result['module_label'] == 'ASAP'
    assert [t['name'] for t in result['templates']] == ['Portable sales']
    template = result['templates'][0]
    assert template['default_revision_id'] == first and template['recording_status'] == 'active'
    assert template['step_count'] == len(list(flow_recording.walk_steps(definition()['steps'])))
    assert [r['id'] for r in template['revisions']] == [second, first]
    assert template['revisions'][1]['status'] == 'active' and template['revisions'][0]['status'] == 'draft'
    # The destination itself and flows without recordings are excluded; search filters by name.
    assert not [t for t in routes.list_templates(source['id'])['templates'] if t['flow_id'] == source['id']]
    assert routes.list_templates(destination['id'], q='zzz')['templates'] == []
    assert empty['id'] not in [t['flow_id'] for t in result['templates']]
    # A GSCM destination sees only GSCM recordings; a pending website change is honoured.
    assert [t['name'] for t in routes.list_templates(gscm['id'])['templates']] == []
    assert [t['name'] for t in routes.list_templates(destination['id'], site_id=gscm_site['id'])['templates']] == ['GSCM flow']
    with pytest.raises(HTTPException, match='GSCM'):
        routes.list_templates(routes.create_draft(routes.RecordingDraft(name='Other', site_id=flows.create_site(flows.SiteWrite(name='Other', adapter='web_export', base_url='http://localhost/x'), _request())['id']), _request())['id'])


def test_preview_and_copy_create_independent_draft_with_provenance_and_no_evidence(flow_db):
    source, job = draft_job()
    site_id = job['site']['id']
    value = definition()
    value['steps'][0]['label'] = 'Open sales'
    download = next(s for s in flow_recording.walk_steps(value['steps']) if s['action'] == 'download')
    download['output']['min_rows'] = 3
    download['steps'][0]['bookmark_target'] = {'kind': 'gscm_favorite', 'bookmark_name': 'Regional sales'}
    value['steps'].insert(1, {'id': 'wait-1', 'page': 'page', 'action': 'wait', 'seconds': 5})
    revision = _save(source['id'], value)
    with database.get_db() as db:
        db.execute("UPDATE flow_recording_revisions SET status='validated', evidence_json='{\"engine_hash\":\"x\"}', config_hash='abc', validated_at='2026-09-07' WHERE id=?", (revision,))
        db.execute('UPDATE flows SET recording_revision_id=? WHERE id=?', (revision, source['id']))
    destination = _second_flow(site_id)
    preview = routes.preview_template(destination['id'], source['id'], revision)
    assert preview['status'] == 'active' and preview['source_name'] == 'Portable sales'
    assert preview['definition']['steps'][1]['action'] == 'wait'
    copied = routes.copy_template(destination['id'], routes.TemplateCopy(source_flow_id=source['id'], source_revision_id=None))
    assert copied['provenance']['source_flow_id'] == source['id'] and copied['provenance']['source_revision_id'] == revision
    assert copied['definition']['parameters'] == value['parameters']
    steps = list(flow_recording.walk_steps(copied['definition']['steps']))
    assert any(s.get('bookmark_target') == {'kind': 'gscm_favorite', 'bookmark_name': 'Regional sales'} for s in steps)
    assert next(s for s in steps if s['action'] == 'download')['output']['min_rows'] == 3
    assert copied['definition']['steps'][0]['label'] == 'Open sales'
    with database.get_db() as db:
        row = db.execute('SELECT * FROM flow_recording_revisions WHERE id=?', (copied['revision_id'],)).fetchone()
        assert row['flow_id'] == destination['id'] and row['status'] == 'draft'
        assert row['evidence_json'] is None and row['config_hash'] is None and row['validated_at'] is None
        assert json.loads(row['template_source_json'])['source_flow_name'] == 'Portable sales'
        flow = db.execute('SELECT recording_revision_id, enabled FROM flows WHERE id=?', (destination['id'],)).fetchone()
        assert flow['recording_revision_id'] is None and flow['enabled'] == 0
        # The copy must pass Test recording before activation.
        with pytest.raises(HTTPException, match='Validate'):
            routes.activate_revision(destination['id'], copied['revision_id'])
    listing = routes.list_recordings(destination['id'])
    assert listing['revisions'][0]['template_source']['source_revision_id'] == revision
    # Later edits or deletion of the source do not affect the copy.
    edited = copy.deepcopy(value); edited['steps'][0]['label'] = 'Changed later'
    _save(source['id'], edited)
    with database.get_db() as db:
        db.execute('DELETE FROM flow_recording_revisions WHERE flow_id=?', (source['id'],))
    listing = routes.list_recordings(destination['id'])
    assert listing['revisions'][0]['definition']['steps'][0]['label'] == 'Open sales'


def test_copy_enforces_module_compatibility_including_pending_website(flow_db):
    source, job = draft_job()
    _save(source['id'], definition())
    gscm_site = flows.create_site(flows.SiteWrite(name='GSCM templates', adapter='gscm_portal', base_url='http://localhost/gscm', auth_url='http://localhost/gscm'), _request())
    gscm = _second_flow(gscm_site['id'], 'GSCM flow')
    with pytest.raises(HTTPException, match='cannot be used for a GSCM Flow'):
        routes.copy_template(gscm['id'], routes.TemplateCopy(source_flow_id=source['id']))
    with pytest.raises(HTTPException, match='cannot be used'):
        routes.preview_template(gscm['id'], source['id'], routes.list_templates(_second_flow(job['site']['id'])['id'])['templates'][0]['default_revision_id'])
    # A pending website change to GSCM on an ASAP flow blocks an ASAP template too.
    asap_destination = _second_flow(job['site']['id'], 'ASAP destination')
    with pytest.raises(HTTPException, match='cannot be used for a GSCM Flow'):
        routes.copy_template(asap_destination['id'], routes.TemplateCopy(source_flow_id=source['id'], site_id=gscm_site['id']))
    with pytest.raises(HTTPException, match='different Flow'):
        routes.copy_template(source['id'], routes.TemplateCopy(source_flow_id=source['id']))
    with pytest.raises(HTTPException, match='no saved recording'):
        routes.copy_template(asap_destination['id'], routes.TemplateCopy(source_flow_id=_second_flow(job['site']['id'], 'Empty')['id']))
    with pytest.raises(HTTPException, match='no longer exists'):
        routes.copy_template(asap_destination['id'], routes.TemplateCopy(source_flow_id=source['id'], source_revision_id=999))


def test_copy_of_an_older_version_and_busy_destination(flow_db, monkeypatch):
    source, job = draft_job()
    first = _save(source['id'], definition())
    changed = definition(); changed['steps'][0]['label'] = 'Newest'
    _save(source['id'], changed)
    destination = _second_flow(job['site']['id'])
    older = routes.copy_template(destination['id'], routes.TemplateCopy(source_flow_id=source['id'], source_revision_id=first))
    assert older['definition']['steps'][0].get('label') is None
    monkeypatch.setattr(routes, '_launch', lambda scan_id: {'scan_id': scan_id})
    routes.start_recording(destination['id'], _request())
    with pytest.raises(HTTPException, match='active recording'):
        routes.copy_template(destination['id'], routes.TemplateCopy(source_flow_id=source['id']))


def test_replicated_flow_copies_recording_as_its_own_draft_and_starts_paused(flow_db):
    source, job = draft_job()
    revision = _save(source['id'], definition())
    with database.get_db() as db:
        db.execute('UPDATE flows SET recording_revision_id=? WHERE id=?', (revision, source['id']))
        original = flows._flow_out(db, source['id'])
    body = flows.FlowWrite.model_validate({**original, 'name': 'Portable sales copy', 'enabled': False, 'recording_revision_id': None, 'target_folder': None})
    replica = flows.create_flow(body, _request())
    assert replica['enabled'] is False and replica['recording_revision_id'] is None and replica['execution_method'] == 'recorded'
    copied = routes.copy_template(replica['id'], routes.TemplateCopy(source_flow_id=source['id']))
    with database.get_db() as db:
        rows = db.execute('SELECT flow_id, status FROM flow_recording_revisions WHERE id=?', (copied['revision_id'],)).fetchone()
        assert dict(rows) == {'flow_id': replica['id'], 'status': 'draft'}
        assert db.execute('SELECT recording_revision_id FROM flows WHERE id=?', (replica['id'],)).fetchone()[0] is None
        assert copied['revision_id'] != revision
