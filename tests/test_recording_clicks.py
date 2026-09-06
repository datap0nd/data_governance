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
from app.flow_recording_clicks import RecordingClickError, click_recorded
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
        button(id, scope, () => {selected = id;
            for(const button of node.querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.id === id));
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


def click(node, title, events=None, **budgets):
    return click_recorded(node, {'action': 'click', 'page': 'page',
        'locator': [{'method': 'get_by_title', 'args': [title]}]}, [], {'timeout': 1000},
        events.append if events is not None else None,
        **{'settle_timeout_ms': 40, 'proof_timeout_ms': 100, 'poll_ms': 10, **budgets})


def test_inert_setting_caption_click_uses_exact_native_owner_once(page):
    events = []
    result = click(page.get_by_title('Setting', exact=True), 'Setting', events)
    assert result['confirmation'] == 'confirmed'
    assert result['native_fallback'] is True
    assert result['component_id'] == GEAR
    assert page.evaluate('calls') == {'dom': [GEAR], 'native': [GEAR]}
    assert events[0]['target']['element_id'] == GEAR + ':icontext'
    assert events[0]['target']['owner_id'] == GEAR
    assert events[-1]['click']['confirmation'] == 'confirmed'
    assert events[-1]['click']['attempt'] == 2
    assert page.locator('[id="' + SETTING + '"]').is_visible()


@pytest.mark.parametrize('mode,delay', [('dom', 0), ('delayed', 100)])
def test_normal_and_delayed_setting_clicks_never_dispatch_native_twice(page, mode, delay):
    page.evaluate('([mode, delay]) => {window.mode = mode; window.delay = delay;}', [mode, delay])
    result = click(page.get_by_title('Setting', exact=True), 'Setting', settle_timeout_ms=250)
    assert result['native_fallback'] is False
    assert page.evaluate('calls') == {'dom': [GEAR], 'native': []}


def test_favorite_and_empty_public_scope_confirm_navigation_without_rows(page):
    page.evaluate('openSetting()')
    favorite = click(page.get_by_title('Favorite', exact=True), 'Favorite')
    public = click(page.get_by_title('Public', exact=True), 'Public')
    assert favorite['evidence']['transition'] == 'favorite_visible'
    assert public['evidence']['transition'] == 'public_selected'
    assert public['native_fallback'] is True
    assert page.evaluate('calls.native') == [FAVORITE, PUBLIC]
    assert page.locator('[id="' + PANEL + '.form.grd_bookmark"]').inner_text() == 'No bookmarks'
    assert page.locator('[id*="gridrow_"]').count() == 0


@pytest.mark.parametrize('title', ['Private', 'Custom'])
def test_other_known_scopes_are_confirmed_without_bookmark_contents(page, title):
    page.evaluate('openSetting(); openFavorite()')
    result = click(page.get_by_title(title, exact=True), title)
    assert result['evidence']['transition'] == title.lower() + '_selected'
    assert result['confirmation'] == 'confirmed'


def test_existing_setting_is_left_for_ordinary_recorded_dispatch(page):
    page.evaluate('openSetting()')
    assert click(page.get_by_title('Setting', exact=True), 'Setting') is None
    assert page.evaluate('calls') == {'dom': [], 'native': []}


def test_native_fallback_stays_inside_recorded_frame_despite_duplicate_outer_control(page):
    page.evaluate('openSetting(); document.body.style.visibility = "hidden"')
    frame = page.locator('body').evaluate_handle("body => {const node = document.createElement('iframe'); node.id = 'recorded'; node.style.visibility='visible'; node.style.width='800px'; node.style.height='500px'; body.append(node); return node;}")
    child = frame.as_element().content_frame()
    child.set_content(FIXTURE)
    result = click(page.frame_locator('#recorded').get_by_title('Setting', exact=True), 'Setting')
    assert result['native_fallback'] is True
    assert child.evaluate('calls.native') == [GEAR]
    assert page.evaluate('calls.native') == []
    frame.dispose()


def test_outer_setting_cannot_confirm_an_inert_frame_click(page):
    page.evaluate('openSetting()')
    page.locator('body').evaluate("body => {const frame = document.createElement('iframe'); frame.id='recorded'; frame.width=800; frame.height=500; body.append(frame);}")
    child = next(frame for frame in page.frames if frame != page.main_frame)
    child.set_content(FIXTURE)
    child.evaluate('window.mode="inert"')
    with pytest.raises(RecordingClickError) as error:
        click(page.frame_locator('#recorded').get_by_title('Setting', exact=True), 'Setting')
    assert error.value.diagnostic['click']['confirmation'] == 'transition_missing'
    assert child.evaluate('calls.native') == [GEAR]
    assert page.evaluate('calls.native') == []


def test_replaced_native_handler_is_never_used_to_repeat_the_click(page):
    page.evaluate('window.mode="stale"')
    with pytest.raises(RecordingClickError) as error:
        click(page.get_by_title('Setting', exact=True), 'Setting')
    assert error.value.diagnostic['click']['confirmation'] == 'target_changed'
    assert page.evaluate('calls.native') == []


def test_unavailable_native_handler_reports_failure_without_replaying_other_controls(page):
    page.evaluate('delete app.mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting.on_fire_onclick')
    with pytest.raises(RecordingClickError) as error:
        click(page.get_by_title('Setting', exact=True), 'Setting')
    assert error.value.diagnostic['click']['confirmation'] == 'native_unavailable'
    assert page.evaluate('calls') == {'dom': [GEAR], 'native': []}


def test_hidden_setting_shell_does_not_count_as_a_successful_transition(page):
    page.evaluate('''() => {openSetting(); document.getElementById("mainframe.VFrameSet.TopFrame.Setting0").style.display='none'; window.mode='inert';}''')
    with pytest.raises(RecordingClickError) as error:
        click(page.get_by_title('Setting', exact=True), 'Setting')
    assert error.value.diagnostic['click']['confirmation'] == 'transition_missing'
    assert page.evaluate('calls.native') == [GEAR]


def test_delayed_actionability_uses_playwright_waiting_without_force(page):
    page.evaluate('''() => {window.mode='dom'; const button=document.getElementById("mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting"); button.disabled=true; setTimeout(()=>button.disabled=false,80);}''')
    result = click(page.get_by_title('Setting', exact=True), 'Setting')
    assert result['confirmation'] == 'confirmed'
    assert page.evaluate('calls') == {'dom': [GEAR], 'native': []}


def test_native_selected_state_confirms_empty_scope_without_dom_selection_attributes(page):
    page.evaluate('''() => {openSetting(); openFavorite();
        const component=app.mainframe.VFrameSet.TopFrame.Setting0.form.div_favorite.form.btn_public;
        component.on_fire_onclick=()=>{calls.native.push("public-native"); selected="mainframe.VFrameSet.TopFrame.Setting0.form.div_favorite.form.btn_public";};
    }''')
    result = click(page.get_by_title('Public', exact=True), 'Public')
    assert result['evidence']['transition'] == 'public_selected'
    assert page.locator('[aria-selected=true],[aria-pressed=true],[userstatus=selected]').count() == 0
    assert page.evaluate('calls.native') == ['public-native']


def test_duplicate_recorded_targets_fail_before_any_dispatch(page):
    page.evaluate('''() => {const copy=document.querySelector('button').cloneNode(true); document.body.append(copy);}''')
    with pytest.raises(Exception, match='strict mode violation'):
        click(page.get_by_title('Setting', exact=True), 'Setting')
    assert page.evaluate('calls') == {'dom': [], 'native': []}


@pytest.mark.parametrize('identifier,title', [('btn_run', 'Run report'), ('btn_excel', 'Download'), ('btn_unknown', 'Setting')])
def test_generic_and_report_actions_are_not_dispatched_or_retried_by_helper(page, identifier, title):
    page.evaluate('([identifier,title])=>{document.body.innerHTML="";button("mainframe.VFrameSet.TopFrame.form.div_main.form."+identifier,title,()=>{});}', [identifier, title])
    assert click(page.get_by_title(title, exact=True), title) is None
    assert page.evaluate('calls') == {'dom': [], 'native': []}


def test_cancellation_from_diagnostic_callback_is_not_swallowed(page):
    class Cancelled(RuntimeError):
        pass

    def cancel(detail):
        if detail['phase'] == 'click_waiting':
            raise Cancelled('cancelled by fixture')

    with pytest.raises(Cancelled, match='cancelled by fixture'):
        click_recorded(page.get_by_title('Setting', exact=True), {'action': 'click'}, [], {'timeout': 1000}, cancel,
            settle_timeout_ms=100, proof_timeout_ms=100, poll_ms=10)
    assert page.evaluate('calls.native') == []


def test_recorded_click_timeout_also_caps_transition_and_fallback(page):
    page.evaluate('window.mode="inert"')
    with pytest.raises(RecordingClickError) as error:
        click_recorded(page.get_by_title('Setting', exact=True), {'action': 'click'}, [], {'timeout': 150},
            settle_timeout_ms=500, proof_timeout_ms=500, poll_ms=10)
    assert error.value.diagnostic['click']['confirmation'] == 'timeout'
    assert page.evaluate('calls.native') == []


@pytest.fixture
def recorded_portal():
    state = {'mode': 'native', 'exports': 0}
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
                content = (FIXTURE + '<script>window.mode=' + json.dumps(state['mode']) + ';</script>').encode()
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
        original = flow_recording_clicks.click_recorded

        def short_proof(*args, **kwargs):
            return original(*args, **kwargs, settle_timeout_ms=40, proof_timeout_ms=100, poll_ms=10)

        monkeypatch.setattr(flow_recording_clicks, 'click_recorded', short_proof)
        profile = tmp_path / 'worker-profile'
        profile.mkdir()
        context = recording_browser.new_context(accept_downloads=True)
        events = []
        try:
            page = context.new_page()
            result = flow_recording_runtime.execute_recorded_flow(page, job,
                lambda status, detail, *args: events.append(detail), profile,
                run_id=23, register_folder=lambda _: {'ops': []})
            assert page.evaluate('calls.native') == [GEAR, FAVORITE, PUBLIC]
        finally:
            context.close()
        assert len(result['artifacts']) == 1
        published = [Path(job['downloads']['target_folder']) / 'recorded.csv']
        for step_id in ('setting', 'favorite', 'public'):
            assert any(event.get('step_id') == step_id
                and event.get('diagnostic', {}).get('click', {}).get('method') == 'nexacro' for event in events)
            assert any(event.get('step_id') == step_id
                and event.get('diagnostic', {}).get('click', {}).get('after', {}).get('open') is True for event in events)
        assert any(event.get('step_id') == 'public'
            and event.get('diagnostic', {}).get('click', {}).get('after', {}).get('selected') is True for event in events)
    assert published[0].read_bytes() == payload
    assert server_state['exports'] == 1
    for step_id in ('setting', 'favorite', 'public'):
        assert any(event.get('step_id') == step_id and event.get('outcome') == 'completed'
            and event.get('confirmation') == 'confirmed' for event in events)
    assert any(event.get('step_id') == 'download-click' and event.get('message') == 'Click sent.' for event in events)
