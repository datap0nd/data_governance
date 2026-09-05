from pathlib import Path
from types import SimpleNamespace

import pytest

from app import flow_folder_access as access, flow_layout


def request(host='192.0.2.1', hostname='worker'):
    return SimpleNamespace(client=SimpleNamespace(host=host), url=SimpleNamespace(hostname=hostname), headers={})


def test_remote_and_session_zero_copy_without_launch(tmp_path, monkeypatch):
    flow = {'id': 1, 'source_type': 'portal', 'target_folder': str(tmp_path)}
    monkeypatch.setattr(access, 'interactive_session', lambda: False)
    for req in [request(), request('127.0.0.1', 'localhost')]:
        assert access.open_folder(flow, None, req) == {'path': str(tmp_path), 'opened': False, 'message': 'Copy this path and open it on the worker PC.'}


def test_local_interactive_opens_only_stored_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(access, 'interactive_session', lambda: True)
    monkeypatch.setattr(access.os, 'startfile', calls.append, raising=False)
    flow = {'id': 1, 'source_type': 'portal', 'target_folder': str(tmp_path)}
    assert access.open_folder(flow, None, request('127.0.0.1', 'localhost'))['opened']
    assert calls == [str(tmp_path)]
    proxied = request('127.0.0.1', 'localhost'); proxied.headers['x-forwarded-for'] = '192.0.2.1'
    assert not access.open_folder(flow, None, proxied)['opened']
    assert len(calls) == 1


def test_foreign_managed_folder_is_not_opened(tmp_path):
    folder = flow_layout.create_flow_folder(str(tmp_path / 'root'), 'web_export', 'Flow', 1)
    with pytest.raises(ValueError, match='another flow'):
        access.open_folder({'id': 2, 'source_type': 'portal', 'flow_folder': str(folder)}, None, request())
