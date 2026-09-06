"""Real browser fixtures for recorded Nexacro navigation, without portal access."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from app import flow_portable, flow_recording, flow_recording_clicks, flow_recording_runtime
from app.flow_recording_clicks import click_recorded
from test_flow_recordings import draft_job
from test_flows import flow_db


GEAR = 'mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting'
SETTING = 'mainframe.VFrameSet.TopFrame.Setting0'
FAVORITE = SETTING + '.form.btn_favorite'
PANEL = SETTING + '.form.div_favorite'
PUBLIC = PANEL + '.form.btn_public'

FIXTURE = r"""<!doctype html><style>
button {display:block;width:140px;height:40px;margin:8px} span {display:inline-block}
.panel {width:500px;min-height:100px;border:1px solid #999}
</style><body><div id="mainframe.VFrameSet"></div><script>
window.calls = {dom: [], native: []};
window.app = {};
window.nexacro = {getApplication: () => app};
const gear = 'mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting';
const setting = 'mainframe.VFrameSet.TopFrame.Setting0';
const favorite = setting + '.form.btn_favorite';
const panel = setting + '.form.div_favorite';
window.mode = 'native'; window.delay = 0; window.selected = null;
function register(id, handler) {
    const parts = id.split('.'), last = parts.pop(); let parent = app;
    for (const part of parts) parent = parent[part] ||= {};
    return parent[last] = {get_enable: () => true, get_visible: () => true,
        getSelectStatus: () => selected === id,
        on_fire_onclick: () => {calls.native.push(id); if(mode !== 'inert') handler();}};
}
function button(id, title, handler, target = document.body) {
    const node = document.createElement('button'); node.id = id;
    const caption = document.createElement('span'); caption.id = id + ':icontext';
    caption.title = title; caption.textContent = title; node.append(caption); target.append(node);
    const component = register(id, handler);
    node.addEventListener('click', () => {
        calls.dom.push(id);
        if(mode === 'dom') handler();
        else if(mode === 'delayed') setTimeout(handler, delay);
        else if(mode === 'stale') component.on_fire_onclick = () => {calls.native.push('replacement'); handler();};
    });
    return node;
}
window.openFavorite = () => {
    if(document.getElementById(panel)) return;
    const node = document.createElement('div'); node.id = panel; node.className = 'panel';
    document.getElementById(setting).append(node);
    for(const scope of ['Private','Public','Custom']) {
        const id = panel + '.form.btn_' + scope.toLowerCase();
        button(id, scope, () => {
            if (!window.noSelectionMarkers) {
                selected = id;
                for(const button of node.querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.id === id));
            }
            if(scope === 'Public' && !node.querySelector('a')) {
                const link = document.createElement('a'); link.href='/export'; link.textContent='Download file'; node.append(link);
            }
        }, node);
    }
    const emptyGrid = document.createElement('div'); emptyGrid.id = panel + '.form.grd_bookmark';
    emptyGrid.textContent = 'No bookmarks'; node.append(emptyGrid);
};
window.openSetting = () => {
    if(document.getElementById(setting)) return;
    const node = document.createElement('div'); node.id = setting; node.className = 'panel';
    document.body.append(node); button(favorite, 'Favorite', openFavorite, node);
};
button(gear, 'Setting', openSetting);
</script>"""


@pytest.fixture(scope='module')
def recording_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(recording_browser):
    page = recording_browser.new_page()
    page.set_content(FIXTURE)
    yield page
    page.close()


def click(node, title, events=None):
    return click_recorded(node, {'action': 'click', 'page': 'page',
        'locator': [{'method': 'get_by_title', 'args': [title]}]}, [], {'timeout': 500},
        events.append if events is not None else None)


def test_inert_caption_dispatches_observed_owning_button_once(page):
    page.evaluate("window.mode='dom'; document.querySelector('button').style.textAlign='left'; document.querySelector('span').addEventListener('click', e=>e.stopPropagation())")
    events = []
    result = click(page.get_by_title('Setting', exact=True), 'Setting', events)
    assert result['confirmation'] == 'not_requested'
    assert result['message'] == 'Click sent.'
    assert page.evaluate('calls') == {'dom': [GEAR], 'native': []}
    assert page.locator('[id="' + SETTING + '"]').is_visible()
    assert events[0]['target']['dispatch_target'] == 'observed_owner'
    assert events[-1]['click']['verification'] == 'none'
    assert events[-1]['click']['retry_policy'] == 'never'


@pytest.mark.parametrize('mode', ['native', 'inert', 'stale', 'delayed'])
def test_missing_transition_does_not_fail_or_repeat_dispatch(page, mode):
    page.evaluate('mode=>{window.mode=mode; window.delay=200;}', mode)
    result = click(page.get_by_title('Setting', exact=True), 'Setting')
    assert result['message'] == 'Click sent.'
    assert page.evaluate('calls') == {'dom': [GEAR], 'native': []}


def test_public_changes_view_without_selection_markers_and_is_not_rejected(page):
    page.evaluate("""() => {openSetting(); openFavorite(); window.mode='inert';
        delete app.mainframe.VFrameSet.TopFrame.Setting0.form.div_favorite.form.btn_public.getSelectStatus;
        document.querySelector('[title=Public]').parentElement.addEventListener('click',()=>{
            const next=document.createElement('button'); next.textContent='Next report'; document.body.append(next);
        });
    }""")
    result = click(page.get_by_title('Public', exact=True), 'Public')
    assert result['message'] == 'Click sent.'
    page.get_by_role('button', name='Next report', exact=True).click()
    assert page.locator('[aria-selected=true],[aria-pressed=true],[userstatus=selected]').count() == 0
    assert page.evaluate('calls') == {'dom': [PUBLIC], 'native': []}


@pytest.mark.parametrize('title', ['Favorite', 'Public', 'Private', 'Custom'])
def test_all_navigation_controls_use_single_dispatch_without_selection_gate(page, title):
    page.evaluate('openSetting(); openFavorite(); window.mode="dom"')
    assert click(page.get_by_title(title, exact=True), title)['confirmation'] == 'not_requested'
    assert len(page.evaluate('calls.dom')) == 1
    assert page.evaluate('calls.native') == []


def test_click_remains_in_recorded_frame_with_duplicate_outer_control(page):
    page.locator('body').evaluate("body => {const frame=document.createElement('iframe'); frame.id='recorded'; frame.width=800; frame.height=500; body.append(frame);}")
    child = next(frame for frame in page.frames if frame != page.main_frame)
    child.set_content(FIXTURE)
    child.evaluate('window.mode="dom"')
    click(page.frame_locator('#recorded').get_by_title('Setting', exact=True), 'Setting')
    assert child.evaluate('calls') == {'dom': [GEAR], 'native': []}
    assert page.evaluate('calls') == {'dom': [], 'native': []}


@pytest.mark.parametrize('condition', ['duplicate', 'hidden', 'disabled', 'missing'])
def test_real_actionability_failure_still_stops_without_native_retry(page, condition):
    changes = {'duplicate': "document.body.append(document.querySelector('button').cloneNode(true))",
        'hidden': "document.querySelector('button').style.display='none'",
        'disabled': "document.querySelector('button').disabled=true",
        'missing': "document.querySelector('button').remove()"}
    page.evaluate(changes[condition])
    with pytest.raises(Exception):
        click(page.get_by_title('Setting', exact=True), 'Setting')
    assert page.evaluate('calls') == {'dom': [], 'native': []}


def test_unavailable_native_handler_is_irrelevant_to_successful_dom_click(page):
    page.evaluate('window.mode="dom"; delete app.mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting.on_fire_onclick')
    assert click(page.get_by_title('Setting', exact=True), 'Setting')['message'] == 'Click sent.'
    assert page.evaluate('calls.native') == []


def test_delayed_actionability_still_uses_playwright(page):
    page.evaluate("window.mode='dom'; document.querySelector('button').disabled=true; setTimeout(()=>document.querySelector('button').disabled=false,80)")
    assert click(page.get_by_title('Setting', exact=True), 'Setting')['message'] == 'Click sent.'
    assert page.evaluate('calls.dom') == [GEAR]


@pytest.mark.parametrize('identifier,title', [('btn_run', 'Run report'), ('btn_excel', 'Download'), ('btn_unknown', 'Setting')])
def test_generic_and_report_actions_are_left_for_normal_dispatch(page, identifier, title):
    page.evaluate('([identifier,title])=>{document.body.innerHTML="";button("mainframe.VFrameSet.TopFrame.form.div_main.form."+identifier,title,()=>{});}', [identifier, title])
    assert click(page.get_by_title(title, exact=True), title) is None
    assert page.evaluate('calls') == {'dom': [], 'native': []}


def test_cancel_before_dispatch_never_clicks(page):
    def cancel(detail):
        raise RuntimeError('cancelled by fixture')
    with pytest.raises(RuntimeError, match='cancelled by fixture'):
        click_recorded(page.get_by_title('Setting'), {'action': 'click'}, [], {'timeout': 500}, cancel)
    assert page.evaluate('calls') == {'dom': [], 'native': []}


@pytest.fixture
def recorded_portal():
    state = {'mode': 'dom', 'exports': 0}
    payload = b'Code,Amount\nRecorded,42\n'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/export':
                state['exports'] += 1
                content = payload
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Content-Disposition', 'attachment; filename="recorded.csv"')
            else:
                content = (FIXTURE + '<script>window.noSelectionMarkers=true;window.mode=' + json.dumps(state['mode']) + ';</script>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/report', state, payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def recorded_job(url):
    _, job = draft_job(url, adapter='gscm_portal')
    steps = [dict(id='open', action='goto', page='page', args=[url])]
    for title in ('Setting', 'Favorite', 'Public'):
        steps.append(dict(id=title.lower(), action='click', page='page',
            locator=[dict(method='get_by_title', args=[title], kwargs={'exact': True})]))
    steps.append(dict(id='download', action='download', page='page', output={'format': 'csv'},
        steps=[dict(id='download-click', action='click', page='page',
            locator=[dict(method='get_by_role', args=['link'], kwargs={'name': 'Download file', 'exact': True})])]))
    value = dict(version=2, timezone='UTC', adapter='gscm_portal', steps=steps, parameters={})
    job['recording'].update(definition=value, definition_hash=flow_recording.digest(value))
    job['recording_parameters'] = {}
    job['execution']['browser_channel'] = 'chrome'
    job['downloads'].update(output_mode='direct_replace', filename_template='recorded.csv',
        file_format='csv', asap_download_type=None, excel_trim='none')
    return job


@pytest.mark.parametrize('portable', [False, True])
def test_recorded_navigation_reaches_one_real_download_in_worker_and_portable(
        flow_db, tmp_path, recorded_portal, recording_browser, monkeypatch, portable):
    url, server_state, payload = recorded_portal
    job = recorded_job(url)
    if portable:
        # Ordinary successful DOM clicks use production budgets and prove the
        # generated script includes the helper, not an in-process monkeypatch.
        server_state['mode'] = 'dom'
        script = tmp_path / 'standalone.py'
        script.write_text(flow_portable.source(job), encoding='utf-8')
        output_root = tmp_path / 'portable-output'
        result = subprocess.run([sys.executable, '-I', str(script), '--headless', '--output-root', str(output_root)],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
            env={**os.environ, 'DG_FLOW_LOCK_ROOT': str(tmp_path / 'execution-locks')})
        assert result.returncode == 0, result.stderr
        log = next(output_root.rglob('*.jsonl'))
        events = [json.loads(line)['progress'] for line in log.read_text().splitlines()]
        published = list(output_root.glob('*/*/Downloads/recorded.csv'))
        assert len(published) == 1
    else:
        profile = tmp_path / 'worker-profile'
        profile.mkdir()
        context = recording_browser.new_context(accept_downloads=True)
        events = []
        try:
            page = context.new_page()
            result = flow_recording_runtime.execute_recorded_flow(page, job,
                lambda status, detail, *args: events.append(detail), profile,
                run_id=23, register_folder=lambda _: {'ops': []})
            assert page.evaluate('calls.dom') == [GEAR, FAVORITE, PUBLIC]
            assert page.evaluate('calls.native') == []
        finally:
            context.close()
        assert len(result['artifacts']) == 1
        published = [Path(job['downloads']['target_folder']) / 'recorded.csv']
    assert published[0].read_bytes() == payload
    assert server_state['exports'] == 1
    for step_id in ('setting', 'favorite', 'public'):
        assert any(event.get('step_id') == step_id and event.get('outcome') == 'completed'
            and event.get('confirmation') == 'not_requested' for event in events)
    assert any(event.get('step_id') == 'download-click' and event.get('message') == 'Click sent.' for event in events)


def test_probe_captures_absent_attributes_hit_blocker_and_duplicate_candidates(page):
    from app import flow_recording_diagnostics as diagnostics
    page.evaluate('''() => {openSetting(); openFavorite();
        const overlay=document.createElement('div'); overlay.id='overlay'; overlay.style='position:fixed;inset:0;z-index:99'; document.body.append(overlay);
    }''')
    step = {'id': 'public', 'action': 'click', 'page': 'page',
            'locator': [{'method': 'get_by_title', 'args': ['Public']}]}
    detail = diagnostics.sanitize_diagnostic({'target': flow_recording_runtime.observe_target(page.get_by_title('Public'), step)})
    target = detail['target']
    assert target['match_count'] == 1
    assert target['frame_url_hash'] and target['document_state'] == 'complete'
    candidate = target['candidates'][0]
    assert candidate['aria_selected'] is None and candidate['aria_pressed'] is None
    assert candidate['hit_id'] == 'overlay' and candidate['hit_is_target'] is False
    assert candidate['width'] > 0 and candidate['tag'] == 'span'
    page.evaluate("document.body.append(document.querySelector('[title=Public]').parentElement.cloneNode(true))")
    detail = flow_recording_runtime.observe_target(page.get_by_title('Public'), step)
    assert detail['match_count'] == 2 and len(detail['candidates']) == 2


def test_failed_next_target_keeps_previous_dispatch_and_technical_exception(
        flow_db, tmp_path, recorded_portal, recording_browser):
    url, state, _ = recorded_portal
    state['mode'] = 'inert'
    job = recorded_job(url)
    job['execution']['recording_wait_seconds'] = 1
    job['recording']['definition']['steps'][2]['kwargs'] = {'timeout': 200}
    job['recording']['definition_hash'] = flow_recording.digest(job['recording']['definition'])
    events = []
    context = recording_browser.new_context(accept_downloads=True)
    try:
        with pytest.raises(Exception):
            flow_recording_runtime.execute_recorded_flow(context.new_page(), job,
                lambda status, detail, *args: events.append(detail), tmp_path,
                run_id=24, register_folder=lambda _: {'ops': []})
    finally:
        context.close()
    failed = next(e['diagnostic'] for e in events if e.get('outcome') == 'failed')
    assert failed['prior_step_id'] == 'setting'
    assert failed['call']['step_id'] == 'favorite'
    assert failed['target']['match_count'] == 0
    assert failed['exception']['type'] == 'TimeoutError'
    assert failed['exception']['stack'] and 'timeout' in failed['exception']['signals']
    assert any(e.get('step_id') == 'setting' and e.get('message') == 'Click sent.' for e in events)
