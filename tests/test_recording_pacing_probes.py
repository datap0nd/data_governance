"""An explicit buffer must precede reads of a not-yet-created input."""
import pytest
import playwright.sync_api

from app import flow_recording_pacing, flow_recording_runtime as runtime


@pytest.mark.parametrize('action', ['fill', 'press_sequentially'])
def test_custom_wait_precedes_locator_password_and_expected_text_probes(monkeypatch, tmp_path, action):
    trace = []
    available = False

    class FinishedProbe(Exception):
        pass

    class DelayedInput:
        def get_attribute(self, name):
            assert available, 'The input does not exist before its buffer.'
            trace.append(('attribute', name))
            return 'text'

        def count(self):
            assert available
            return 0  # Optional structural diagnostics must not affect replay.

        def fill(self, value, **kwargs):
            assert available
            trace.append(('dispatch', value))
            raise FinishedProbe()

        press_sequentially = fill

    class ExpectedInput:
        def to_have_text(self, text):
            assert available, 'Expected-text checks must also follow the buffer.'
            trace.append(('expected', text))

    node = DelayedInput()

    def locate(pages, step):
        assert available, 'No target resolution may precede the requested buffer.'
        trace.append(('locate', step['id']))
        return node

    def finish_wait(seconds, update):
        nonlocal available
        trace.append(('wait', seconds))
        update(seconds)
        available = True
        update(0)

    monkeypatch.setattr(runtime, 'locate', locate)
    monkeypatch.setattr(flow_recording_pacing, 'wait', finish_wait)
    monkeypatch.setattr(playwright.sync_api, 'expect', lambda _: ExpectedInput())
    locator = [{'method': 'get_by_label', 'args': ['Region'], 'kwargs': {}}]
    definition = {'version': 2, 'timezone': 'UTC', 'parameters': {}, 'steps': [
        {'id': 'input', 'page': 'page', 'action': action, 'locator': locator,
         'args': ['North'], 'delay_before_seconds': 60, 'expected_text': 'Ready'},
        {'id': 'download', 'page': 'page', 'action': 'download', 'output': {'format': 'csv'},
         'steps': [{'id': 'export', 'page': 'page', 'action': 'click', 'locator': locator}]}]}
    job = {'recording': {'revision': 1, 'definition': definition},
           'recording_parameters': {}, 'execution': {'recording_wait_seconds': 10}}
    events = []
    with pytest.raises(FinishedProbe):
        runtime.acquire(object(), job, lambda status, detail, *_: events.append(detail),
                        tmp_path, tmp_path / 'staging', target=tmp_path / 'output', run_id=1, artifacts=[])
    assert trace == [('wait', 60), ('locate', 'input'), ('attribute', 'type'),
                     ('expected', 'Ready'), ('dispatch', 'North')]
    waiting = next(event for event in events if event.get('remaining_seconds') == 60)
    assert waiting['step_outcomes']['input']['remaining_seconds'] == 60
    assert waiting['step_outcomes']['input']['effective_wait_seconds'] == 60
    assert events[-1]['step_id'] == 'input'
    assert events[-1]['outcome'] == 'failed'
