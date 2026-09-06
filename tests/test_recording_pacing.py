"""Recording timing contracts and cancellation without minutes of test sleeps."""
import pytest

from app import flow_recording, flow_recording_pacing as pacing
from test_flow_recordings import definition


def action(name='click', **kwargs):
    return {'id': name, 'page': 'page', 'action': name, **kwargs}


@pytest.mark.parametrize('value', [0, -1, 601, 1.5, True, None, '10'])
def test_step_wait_rejects_nonpositive_or_noninteger_values(value):
    recorded = definition()
    step = next(s for s in flow_recording.walk_steps(recorded['steps']) if s['action'] == 'click')
    step['delay_before_seconds'] = value
    with pytest.raises(ValueError, match='Wait before this step'):
        flow_recording.validate_definition(recorded)


@pytest.mark.parametrize('name', ['goto', 'new_page', 'assert', 'download'])
def test_wait_override_only_belongs_to_actual_interaction(name):
    recorded = definition()
    next(s for s in flow_recording.walk_steps(recorded['steps']) if s['action'] == name)['delay_before_seconds'] = 10
    with pytest.raises(ValueError, match='Wait before this step'):
        flow_recording.validate_definition(recorded)


def test_default_ten_positive_override_and_explicit_wait_credit():
    timer = pacing.Pacing({})
    assert timer.interaction(action())['waited_seconds'] == 10
    timer.credit = 5
    assert timer.interaction(action())['waited_seconds'] == 5
    assert timer.credit == 0
    timer.credit = 60
    assert timer.interaction(action())['waited_seconds'] == 0
    assert timer.interaction(action(delay_before_seconds=60))['waited_seconds'] == 60
    assert timer.interaction(action())['waited_seconds'] == 10


def test_nested_event_budget_counts_waits_once_without_consuming_credit():
    timer = pacing.Pacing({'execution': {'recording_wait_seconds': 10}})
    timer.credit = 3
    children = [action('goto'), action('wait', seconds=5), action(),
                action('popup', steps=[action('wait', seconds=60), action(delay_before_seconds=180)]),
                action('fill')]
    # 5 explicit + 2 inherited + 60 explicit + 120 custom + 10 inherited.
    assert timer.event_budget_ms(children) == 197_000
    assert timer.credit == 3
    assert timer.event_budget_ms(children) == 197_000


def test_frozen_job_timing_does_not_depend_on_subsequent_preferences():
    original = {'execution': {'recording_wait_seconds': 14}}
    timer = pacing.Pacing(original)
    original['execution']['recording_wait_seconds'] = 60
    assert timer.interaction(action())['waited_seconds'] == 14


class Clock:
    elapsed = 0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        assert 0 < seconds <= 0.25
        self.elapsed += seconds


def test_countdown_runs_ten_seconds_and_stays_responsive(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(pacing, 'time', clock)
    updates = []
    pacing.wait(10, updates.append)
    assert updates == list(range(10, -1, -1))
    assert clock.elapsed == 10


def test_cancellation_during_long_buffer_never_reaches_action(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(pacing, 'time', clock)

    class Cancelled(Exception):
        pass

    def update(remaining):
        if remaining == 599:
            raise Cancelled()

    with pytest.raises(Cancelled):
        pacing.wait(600, update)
    assert clock.elapsed == 1


def test_real_browser_waits_default_then_override_and_sends_each_click_once(flow_db, tmp_path, report_server):
    from playwright.sync_api import sync_playwright
    from app import flow_browser, flow_recording_runtime as runtime
    from test_flow_recordings import draft_job

    _, job = draft_job(report_server, wait_seconds=10)
    generate = action('click', locator=[{'method': 'get_by_role', 'args': ['button'], 'kwargs': {'name': 'Generate', 'exact': True}}])
    generate['id'] = 'generate'
    trigger = action('click', delay_before_seconds=2,
                     locator=[{'method': 'get_by_role', 'args': ['button'], 'kwargs': {'name': 'Download', 'exact': True}}])
    trigger['id'] = 'download-click'
    job['recording']['definition'] = {'version': 2, 'timezone': 'UTC', 'parameters': {}, 'steps': [
        action('goto', args=[report_server]), generate,
        action('download', output={'format': 'csv'}, steps=[trigger])]}
    job['recording_parameters'] = {}
    events = []
    profile = tmp_path / 'pacing-browser'
    with sync_playwright() as playwright:
        context = flow_browser.launch(playwright, profile, 'chrome', headed=False, downloads=profile / 'downloads')
        try:
            context.add_init_script("window.clicks=[]; document.addEventListener('click', e=>window.clicks.push({name:e.target.textContent,at:performance.now()}), true)")
            page = context.pages[0]
            runtime.execute_recorded_flow(page, job, lambda status, detail, *args: events.append(detail), profile,
                                          run_id=80, register_folder=lambda _: {'ops': []})
            clicks = page.evaluate('window.clicks')
        finally:
            context.close()
    assert [event['name'] for event in clicks] == ['Generate', 'Download']
    assert clicks[0]['at'] >= 9900
    assert clicks[1]['at'] - clicks[0]['at'] >= 1900
    sent = [event for event in events if event.get('message') == 'Click sent.']
    assert len(sent) == 2
    assert all(event['confirmation'] == 'unconfirmed' for event in sent)
    waits = [event['remaining_seconds'] for event in events if event.get('step_id') == 'generate' and 'remaining_seconds' in event]
    assert waits[0] == 10 and waits[-1] == 0
    nested = next(event for event in events if event.get('step_id') == 'download-click' and event.get('remaining_seconds') == 2)
    assert nested['step_outcomes']['download-click']['remaining_seconds'] == 2
    assert nested['step_outcomes']['download-click']['effective_wait_seconds'] == 2


# Reuse the suite's isolated database and harmless local report server.
from test_flows import flow_db
from test_flow_recordings import report_server
