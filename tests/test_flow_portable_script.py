"""The generated run_flow.py is readable, current after every save, and troubleshootable."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import database, flow_handover, flow_portable, flow_standalone as standalone
from app.routers import flows
from test_flows import flow_db, _request
from test_flow_standalone import local_job
from test_flow_recordings import draft_job
from test_recording_timing_settings import activate_test_revision


def _launcher(saved):
    return Path(saved['standalone']['launcher'])


def _flow_block(text):
    return text.split('FLOW = json.loads(r"""', 1)[1].split('""")', 1)[0]


def test_sections_are_verbatim_python_with_comments_and_readable_flow(flow_db, tmp_path):
    saved, job = local_job(tmp_path)
    text = _launcher(saved).read_text(encoding='utf-8')
    assert text.startswith(flow_portable.CATALOG_HEADER)
    assert 'HOW TO TROUBLESHOOT AND PORT A FIX' in text and 'SOURCES[' not in text
    assert text.count('from __future__ import annotations') == 1
    sections = flow_portable.split_sections(text)
    assert {'flow_recording_runtime', 'flow_worker', 'flow_standalone', 'config'} <= set(sections)
    # A comment from the application source survives because declarations are copied, not unparsed.
    assert "# Reuse the worker's initial page; additional pages are explicit." in sections['flow_recording_runtime'][1]
    assert 'from _mf import flow_recording' in sections['flow_recording_runtime'][1]
    body = _flow_block(text)
    assert '\n  "flow": {' in body
    frozen = standalone.freeze(job)
    frozen.pop('recording_parameters', None)
    saved_flow = json.loads(body)
    saved_flow.pop('handover')
    assert saved_flow == frozen


def test_configuration_values_that_look_like_generator_tokens_survive(flow_db, tmp_path):
    _, job = local_job(tmp_path)
    job['flow']['name'] = '__ENTRY_FUNCTION__ __ENTRY_MODULE__ __FLOW_JSON__'
    text = flow_portable.source(job)
    assert json.loads(_flow_block(text))['flow']['name'] == '__ENTRY_FUNCTION__ __ENTRY_MODULE__ __FLOW_JSON__'
    assert 'from _mf.flow_standalone import offline_main\n    sys.exit(offline_main(FLOW))' in text


def test_tracebacks_name_run_flow_lines(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    script = tmp_path / 'copy.py'
    script.write_bytes(_launcher(saved).read_bytes())
    code = '''import json, runpy, sys, traceback
runpy.run_path(sys.argv[1])
from _mf import flow_recording_runtime as runtime
try:
    runtime.locate({}, {'page': 'missing'})
except KeyError:
    frame = traceback.extract_tb(sys.exc_info()[2])[-1]
    print(json.dumps({'filename': frame.filename, 'lineno': frame.lineno, 'line': frame.line}))
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(script)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    frame = json.loads(result.stdout)
    assert Path(frame['filename']).resolve() == script.resolve()
    assert frame['line'] == "node = pages[target['page']]"
    assert script.read_text(encoding='utf-8').split('\n')[frame['lineno'] - 1].strip() == frame['line']


def test_edited_script_is_archived_and_refreshed_on_save(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    script = _launcher(saved)
    generated = script.read_text(encoding='utf-8')
    script.write_text('# my manual edits\n', encoding='utf-8')
    assert flows.standalone_status(saved['id'])['state'] == 'modified'
    result = flow_handover.synchronize(database.DB_PATH, flow_id=saved['id'])[saved['id']]
    assert result['state'] == 'current'
    archived = Path(result['archived_edit'])
    assert archived.parent == script.parent / 'versions'
    assert archived.name.startswith('run_flow-') and '-edited-' in archived.name and archived.suffix == '.py'
    assert archived.read_text(encoding='utf-8') == '# my manual edits\n'
    assert script.read_text(encoding='utf-8') == generated
    assert flows.standalone_status(saved['id'])['state'] == 'current'
    again = flow_handover.synchronize(database.DB_PATH, flow_id=saved['id'])[saved['id']]
    assert again['state'] == 'current' and 'archived_edit' not in again
    assert len(list((script.parent / 'versions').glob('run_flow-*-edited-*.py'))) == 1


def test_modified_archive_revision_is_set_aside_not_blocking(flow_db, tmp_path):
    saved, job = local_job(tmp_path)
    revision = Path(saved['standalone']['script_revision'])
    revision.write_text('# tampered archive\n', encoding='utf-8')
    result = flow_portable.generate(job)
    assert result['state'] == 'current'
    assert Path(result['script_revision']).read_text(encoding='utf-8') == Path(result['launcher']).read_text(encoding='utf-8')
    aside = list(revision.parent.glob(revision.stem + '-edited-*.py'))
    assert [path.read_text(encoding='utf-8') for path in aside] == ['# tampered archive\n']


def test_draft_script_archives_edits_and_still_refuses_to_run(flow_db):
    saved, _ = draft_job()
    assert saved['standalone']['state'] == 'draft'
    script = _launcher(saved)
    script.write_text('# exploring the draft\n', encoding='utf-8')
    result = flow_handover.synchronize(database.DB_PATH, flow_id=saved['id'], force=True)[saved['id']]
    assert result['state'] == 'draft'
    assert Path(result['archived_edit']).read_text(encoding='utf-8') == '# exploring the draft\n'
    run = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and 'cannot run yet' in run.stderr


def test_execution_core_change_refreshes_script_instead_of_drafting(flow_db):
    with pytest.MonkeyPatch.context() as patch:
        # The revision was tested with an older execution core than the current code.
        patch.setattr(flow_portable, 'execution_hash', lambda: 'e' * 64)
        saved, job = draft_job()
        activate_test_revision(saved, job)
    assert flow_portable.execution_hash() != 'e' * 64
    with database.get_db() as db:
        built = flows._build_job(db, saved['id'])
    assert built['recording']['engine_hash'] == 'e' * 64
    result = flow_handover.synchronize(database.DB_PATH, flow_id=saved['id'], force=True)[saved['id']]
    assert result['state'] == 'current', result
    text = Path(result['launcher']).read_text(encoding='utf-8')
    assert text.startswith(flow_portable.HEADER)
    assert f'was last tested with execution core {"e" * 64} (an earlier version of the code).' in text
    assert flows.standalone_status(saved['id'])['state'] == 'current'
    run = subprocess.run([sys.executable, '-I', result['launcher'], '--dry-run'], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)['flow'] == 'Portable sales'


def test_failure_prints_traceback_and_progress_is_echoed_unless_quiet(flow_db, tmp_path, capsys):
    _, job = local_job(tmp_path)
    standalone.run(job, echo=True)
    err = capsys.readouterr().err
    echoed = [line for line in err.splitlines() if line.startswith('[')]
    assert echoed and any(line.startswith('[complete] ') for line in echoed)
    standalone.run(job)
    assert not [line for line in capsys.readouterr().err.splitlines() if line.startswith('[')]
    job['sql_handoff']['enabled'] = True
    job['post_sql_refresh'] = {'mode': 'automatic', 'views': [], 'blocked': 'table missing from metadata'}
    assert standalone.offline_main(job, ['--quiet']) == 1
    err = capsys.readouterr().err
    assert 'Traceback (most recent call last)' in err and 'Standalone Flow failed: Refresh materialized views is blocked' in err
