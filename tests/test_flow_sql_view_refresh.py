"""Regression checks for the actual SQL loader and downstream refresh boundary.

All connections and browser acquisition are synthetic; these tests never contact
a PostgreSQL server or portal.
"""
import copy
import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from app import flow_recording_runtime, flow_recorder_worker, flow_sql, flow_worker
from app import flow_view_refresh as refresh
from test_flows import flow_db
from test_flow_standalone import local_job
from test_flow_recordings import draft_job
from test_view_refresh import FakeEngines, view


class SqlRefreshDatabase:
    """Runs the real loader/executor with a synthetic DBAPI transaction boundary."""

    def __init__(self, failure=None):
        self.failure = failure
        self.events = []
        self.insert_committed = False

    def __call__(self, database):
        owner = self

        class Transaction:
            def commit(self):
                owner.events.append('INSERT COMMIT')
                if owner.failure == 'commit':
                    raise RuntimeError('synthetic SQL commit failure')
                owner.insert_committed = True

            def rollback(self):
                owner.events.append('INSERT ROLLBACK')

        class Cursor:
            def copy_expert(self, statement, stream):
                owner.events.append(str(statement))
                assert 'Abc' in stream.read()
                if owner.failure == 'copy':
                    raise RuntimeError('synthetic COPY failure')

            def close(self):
                pass

        class Connection:
            connection = SimpleNamespace(cursor=Cursor)

            def begin(self):
                return Transaction()

            def execute(self, statement, params=None):
                sql = str(statement)
                owner.events.append(sql)
                if 'REFRESH MATERIALIZED VIEW' in sql:
                    assert owner.insert_committed, 'A view refreshed before SQL committed'
                    if owner.failure == 'refresh':
                        raise RuntimeError('synthetic refresh lock timeout')
                return SimpleNamespace(
                    fetchall=lambda: [('code', 'YES', None, 'NO', 'NEVER')],
                    fetchone=lambda: ('m', 'flow_writer', True),
                )

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class Engine:
            def connect(self):
                return Connection()

            @contextmanager
            def begin(self):
                owner.events.append('REFRESH BEGIN')
                try:
                    yield Connection()
                except Exception:
                    owner.events.append('REFRESH ROLLBACK')
                    raise
                else:
                    owner.events.append('REFRESH COMMIT')

            def dispose(self):
                pass

        return Engine()


@pytest.mark.parametrize('surface', ['file', 'recorded'])
@pytest.mark.parametrize('mode', ['append', 'replace'])
@pytest.mark.parametrize('failure', [None, 'copy', 'commit', 'refresh'])
def test_actual_loader_only_refreshes_after_commit(flow_db, tmp_path, monkeypatch, surface, mode, failure):
    if surface == 'file':
        _, job = local_job(tmp_path)
    else:
        _, job = draft_job()
        source = tmp_path / 'recorded.csv'
        source.write_text('Code\nAbc\n')

        def acquire(*args, artifacts, **kwargs):
            artifacts.append({'file_path': str(source), 'row_count': 1, 'status': 'saved'})

        monkeypatch.setattr(flow_recording_runtime, 'acquire', acquire)
        monkeypatch.setattr(flow_worker, '_publish_direct_artifacts', lambda job, items, **kwargs: items)
    job['sql_handoff'] = {'enabled': True, 'database': 'analytics', 'schema': 'public',
                          'table': 'flow_table', 'mode': mode}
    job['post_sql_refresh'] = {'mode': 'manual', 'views': [view('bi', 'mv_a'), view('bi', 'mv_b')]}
    database = SqlRefreshDatabase(failure)
    monkeypatch.setattr(flow_sql, '_engine', database)
    details, state = [], {}

    def execute():
        return flow_worker.execute_flow(None, job, lambda status, detail, *args, **kwargs:
            details.append((status, copy.deepcopy(detail))), tmp_path / 'profile', run_id=100,
            register_folder=lambda folder: {'ops': []}, state=state)

    if failure in {'copy', 'commit'}:
        with pytest.raises(flow_sql.SqlHandoffError):
            execute()
        assert not any('REFRESH' in event for event in database.events)
        assert 'sql_insertion_complete' not in [detail['stage'] for _, detail in details]
        assert not any(status == 'succeeded' for status, _ in details)
    elif failure == 'refresh':
        with pytest.raises(refresh.ViewRefreshError):
            execute()
        assert database.insert_committed
        assert database.events.count('INSERT COMMIT') == 1
        assert 'INSERT ROLLBACK' not in database.events
        assert 'REFRESH ROLLBACK' in database.events
        assert [item['status'] for item in state['view_refresh_results']] == ['failed', 'skipped']
        assert not any(status == 'succeeded' for status, _ in details)
    else:
        execute()
        statements = database.events
        refreshes = [event for event in statements if event.startswith('REFRESH MATERIALIZED VIEW')]
        assert refreshes == ['REFRESH MATERIALIZED VIEW "bi"."mv_a"', 'REFRESH MATERIALIZED VIEW "bi"."mv_b"']
        assert statements.index('INSERT COMMIT') < statements.index(refreshes[0])
        assert statements.count('REFRESH COMMIT') == 2
        stages = [detail['stage'] for _, detail in details]
        assert stages.index('sql_commit') < stages.index('sql_insertion_complete') < stages.index('view_refresh')
        assert details[-1][0] == 'succeeded'


def test_engine_creation_failure_retains_refresh_checkpoint_and_failed_phase(tmp_path):
    engines = FakeEngines()
    checkpoint = tmp_path / 'checkpoint.json'
    views = [view('bi', 'first'), view('bi', 'second', 'unavailable'), view('bi', 'last')]
    state, timings, details = {}, [{'phase': 'total', 'status': 'running'}], []

    def factory(database):
        if database == 'unavailable':
            raise RuntimeError('synthetic driver configuration failure')
        return engines(database)

    with pytest.raises(refresh.ViewRefreshError) as failure:
        refresh.execute_after_sql({'post_sql_refresh': {'views': views}},
            lambda status, detail, *args: details.append(detail), [], timings, state,
            checkpoint=checkpoint, engine_factory=factory)
    assert [item['status'] for item in failure.value.results] == ['succeeded', 'failed', 'skipped']
    assert state['view_refresh_results'] == failure.value.results
    assert refresh.read_checkpoint(checkpoint) == [refresh.view_key(views[0])]
    assert details[-1]['stage'] == 'view_refresh_failed'
    assert details[-1]['sql_committed'] is True
    assert timings[0]['phase'] == 'view_refresh' and timings[0]['status'] == 'failed'


def _stub_recorded_browser(monkeypatch):
    monkeypatch.setattr(flow_worker, '_exclusive_worker_lock', lambda profile: nullcontext(True))
    monkeypatch.setattr(flow_worker, 'sync_playwright', lambda: nullcontext(None))
    monkeypatch.setattr(flow_recorder_worker, 'browser_session', lambda *args, **kwargs:
        nullcontext((SimpleNamespace(new_page=lambda: None), None)))
    monkeypatch.setattr(flow_recorder_worker, 'authenticate', lambda *args, **kwargs: None)


def test_portable_recorded_refresh_retry_keeps_committed_sql_and_skips_browser(flow_db, tmp_path, monkeypatch, capsys):
    _, job = draft_job()
    job['sql_handoff'] = {'enabled': True, 'database': 'analytics', 'schema': 'public', 'table': 'flow_table'}
    job['post_sql_refresh'] = {'mode': 'manual', 'views': [view('bi', 'mv_a'), view('bi', 'mv_b')]}
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    _stub_recorded_browser(monkeypatch)
    engines = FakeEngines(fail={'mv_b'})
    monkeypatch.setattr(flow_sql, '_engine', engines)
    insertions = []

    def run(page, frozen_job, progress, *args, **kwargs):
        insertions.append('sql')
        progress('running', {'stage': 'sql_insertion'})
        progress('running', {'stage': 'sql_insertion_complete', 'rows_written': 1})
        refresh.execute_after_sql(frozen_job, progress, [], [], {}, checkpoint=refresh.checkpoint_path(frozen_job))

    monkeypatch.setattr(flow_recording_runtime, 'execute_recorded_flow', run)
    assert flow_recording_runtime.standalone_main(job, []) == 1
    assert 'SQL insertion had already committed' in capsys.readouterr().err
    checkpoint = refresh.checkpoint_path(job)
    journal = checkpoint.with_name('sql-outcome.json')
    assert json.loads(journal.read_text())['outcome'] == 'committed'
    assert json.loads(journal.read_text())['refresh_views'] == job['post_sql_refresh']['views']
    assert refresh.read_checkpoint(checkpoint) == [refresh.view_key(view('bi', 'mv_a'))]
    assert flow_recording_runtime.standalone_main(job, []) == 1
    assert '--retry-views' in capsys.readouterr().err
    # A failed retry stays failed and preserves both the barrier and checkpoint.
    monkeypatch.setattr(flow_worker, 'sync_playwright', lambda: pytest.fail('Retry opened a browser'))
    assert flow_recording_runtime.standalone_main(job, ['--retry-views']) == 1
    assert journal.is_file() and checkpoint.is_file()
    engines.fail.clear()
    engines.events.clear()
    assert flow_recording_runtime.standalone_main(job, ['--retry-views']) == 0
    assert insertions == ['sql']
    assert not journal.exists() and not checkpoint.exists()
    assert [s for _, s in engines.events if s.startswith('REFRESH MATERIALIZED VIEW')] == [
        'REFRESH MATERIALIZED VIEW "bi"."mv_b"']


@pytest.mark.parametrize('journal_contents', ['{"outcome":"unknown"}', '{invalid', '[]', '{"outcome":"committed","target":{}}'])
def test_portable_recorded_retry_refuses_unknown_or_different_sql_outcome(flow_db, tmp_path, monkeypatch, capsys, journal_contents):
    _, job = draft_job()
    job['sql_handoff']['enabled'] = True
    job['post_sql_refresh'] = {'mode': 'manual', 'views': [view('bi', 'mv_a')]}
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    checkpoint = refresh.checkpoint_path(job)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({'views': []}))
    journal = checkpoint.with_name('sql-outcome.json')
    journal.write_text(journal_contents)
    monkeypatch.setattr(flow_worker, 'execute_flow', lambda *args, **kwargs: pytest.fail('Unreconciled run executed'))
    assert flow_recording_runtime.standalone_main(job, ['--retry-views']) == 1
    assert 'requires reconciliation' in capsys.readouterr().err
    assert journal.read_text() == journal_contents and checkpoint.is_file()


def _saved_recorded_refresh_retry(tmp_path, monkeypatch):
    _, job = draft_job()
    job['sql_handoff'] = {'enabled': True, 'database': 'analytics', 'schema': 'public', 'table': 'flow_table'}
    views = [view('bi', 'mv_a'), view('bi', 'mv_b')]
    job['post_sql_refresh'] = {'mode': 'manual', 'views': views}
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    checkpoint = refresh.checkpoint_path(job)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({'views': [
        {**item, 'key': refresh.view_key(item), 'status': status}
        for item, status in zip(views, ['succeeded', 'failed'])
    ]}))
    journal = checkpoint.with_name('sql-outcome.json')
    journal.write_text(json.dumps({'run_id': 'original', 'outcome': 'committed',
                                  'target': job['sql_handoff'], 'refresh_views': views}))
    return job, journal, checkpoint


@pytest.mark.parametrize('change', ['removed', 'replaced', 'reordered'])
def test_recorded_retry_rejects_edited_script_plan_without_erasing_recovery(flow_db, tmp_path, monkeypatch, capsys, change):
    job, journal, checkpoint = _saved_recorded_refresh_retry(tmp_path, monkeypatch)
    saved_journal, saved_checkpoint = journal.read_text(), checkpoint.read_text()
    views = job['post_sql_refresh']['views']
    if change == 'removed':
        job['post_sql_refresh']['views'] = views[:1]
    elif change == 'replaced':
        job['post_sql_refresh']['views'] = [views[0], view('bi', 'mv_c')]
    else:
        job['post_sql_refresh']['views'] = list(reversed(views))
    monkeypatch.setattr(flow_worker, 'execute_flow', lambda *args, **kwargs: pytest.fail('Changed plan executed'))
    assert flow_recording_runtime.standalone_main(job, ['--retry-views']) == 1
    assert 'original materialized-view plan' in capsys.readouterr().err
    assert journal.read_text() == saved_journal and checkpoint.read_text() == saved_checkpoint


@pytest.mark.parametrize('legacy', ['missing_journal', 'missing_plan', 'invalid_plan'])
def test_recorded_retry_requires_a_bound_committed_journal(flow_db, tmp_path, monkeypatch, capsys, legacy):
    job, journal, checkpoint = _saved_recorded_refresh_retry(tmp_path, monkeypatch)
    if legacy == 'missing_journal':
        journal.unlink()
    else:
        saved = json.loads(journal.read_text())
        if legacy == 'missing_plan':
            saved.pop('refresh_views')
        else:
            saved['refresh_views'] = {'views': job['post_sql_refresh']['views']}
        journal.write_text(json.dumps(saved))
    saved_journal = journal.read_text() if journal.exists() else None
    saved_checkpoint = checkpoint.read_text()
    monkeypatch.setattr(flow_worker, 'execute_flow', lambda *args, **kwargs: pytest.fail('Unbound recovery executed'))
    assert flow_recording_runtime.standalone_main(job, ['--retry-views']) == 1
    assert 'requires reconciliation' in capsys.readouterr().err
    assert (journal.read_text() if journal.exists() else None) == saved_journal
    assert checkpoint.read_text() == saved_checkpoint


@pytest.mark.parametrize('corruption', ['invalid_json', 'missing_view', 'changed_view', 'reordered', 'wrong_key', 'invalid_status'])
def test_recorded_retry_rejects_checkpoint_mismatch_even_with_unchanged_script(flow_db, tmp_path, monkeypatch, capsys, corruption):
    job, journal, checkpoint = _saved_recorded_refresh_retry(tmp_path, monkeypatch)
    saved = json.loads(checkpoint.read_text())
    if corruption == 'invalid_json':
        checkpoint.write_text('{invalid')
    else:
        if corruption == 'missing_view':
            saved['views'].pop()
        elif corruption == 'changed_view':
            saved['views'][1]['name'] = 'different_view'
        elif corruption == 'reordered':
            saved['views'].reverse()
        elif corruption == 'wrong_key':
            saved['views'][1]['key'] = saved['views'][0]['key']
        else:
            saved['views'][1]['status'] = 'unknown'
        checkpoint.write_text(json.dumps(saved))
    saved_journal, saved_checkpoint = journal.read_text(), checkpoint.read_text()
    monkeypatch.setattr(flow_worker, 'execute_flow', lambda *args, **kwargs: pytest.fail('Untrusted checkpoint executed'))
    assert flow_recording_runtime.standalone_main(job, ['--retry-views']) == 1
    assert 'checkpoint requires reconciliation' in capsys.readouterr().err
    assert journal.read_text() == saved_journal and checkpoint.read_text() == saved_checkpoint
