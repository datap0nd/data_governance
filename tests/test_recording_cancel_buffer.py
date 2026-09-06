"""The worker must honor the real progress acknowledgement during a buffer."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app import flow_worker, flow_recorder_worker as recorder, flow_recording_pacing as pacing


@pytest.mark.parametrize('acknowledgement', [
    {'cancel_requested': True, 'status': 'running'},
    {'ignored': True, 'status': 'cancelled'},
    {'ignored': True, 'status': 'failed'},
])
def test_worker_acknowledges_cancel_and_never_dispatches_after_buffer(tmp_path, monkeypatch, acknowledgement):
    statuses, actions, remaining = [], [], []
    scan = {'id': 1, 'job': {'recording_operation': 'validate', 'browser_channel': 'chrome'}}

    def api(client, method, path, body=None):
        if path.endswith('/register'):
            return {}
        if path.endswith('/claim'):
            return {'scan': scan}
        statuses.append(body['status'])
        if body.get('progress', {}).get('stage') == 'recorded_action':
            remaining.append(body['progress']['remaining_seconds'])
            return acknowledgement
        return {}

    def validate(scan, page, profile, progress):
        pacing.wait(600, lambda left: progress('running', {
            'stage': 'recorded_action', 'message': 'Waiting before click.', 'remaining_seconds': left}))
        actions.append('click')
        return {}

    monkeypatch.setattr(flow_worker, '_api', api)
    monkeypatch.setattr(flow_worker, 'sync_playwright', lambda: nullcontext(object()))
    monkeypatch.setattr(recorder, 'browser_session', lambda *a, **k: nullcontext((SimpleNamespace(pages=[object()]), tmp_path)))
    monkeypatch.setattr(recorder, 'reservation_heartbeat', lambda *a, **k: nullcontext())
    monkeypatch.setattr(recorder, 'validate', validate)
    flow_worker.run_worker('http://localhost:1', 'recorder', 'Recorder', tmp_path, True, True)
    assert statuses == ['running', 'running', 'cancelled']
    assert remaining == [600]
    assert actions == []
