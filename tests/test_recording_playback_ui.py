"""Fictional browser evidence for production playback controls and debug-log UI."""
import json

import pytest

from test_optional_recording_editor import optional_editor


@pytest.fixture
def playback_editor(optional_editor):
    page = optional_editor
    page.goto(page.url.replace('optional.html', 'playback.html'))
    page.get_by_role('button', name='Test recording', exact=True).wait_for()
    return page


def saved_definition(page):
    return page.evaluate('()=>playbackPreview.data.revisions[0].definition')


def test_step_wait_default_custom_wrap_move_undo_and_clear(playback_editor):
    page = playback_editor
    page.get_by_role('button', name='Click “Run report”', exact=True).click()
    assert page.get_by_label('Wait before this step').input_value() == 'default'
    assert page.get_by_role('option', name='Use default · 10 seconds', exact=True).count() == 1
    page.get_by_label('Wait before this step').select_option('custom')
    page.get_by_label('Wait in seconds', exact=True).fill('60')
    page.get_by_label('Wait in seconds', exact=True).press('Tab')
    page.get_by_label('This action produces a download').check()
    assert page.get_by_label('Wait in seconds', exact=True).input_value() == '60'
    page.get_by_role('button', name='Move up', exact=True).click()
    page.get_by_role('button', name='Undo', exact=True).click()
    page.get_by_role('button', name='Save draft', exact=True).click()
    wrapper = next(step for step in saved_definition(page)['steps'] if any(child['id'] == 'run' for child in step.get('steps', [])))
    assert 'delay_before_seconds' not in wrapper
    assert wrapper['steps'][0]['delay_before_seconds'] == 60
    page.get_by_label('This action produces a download').uncheck()
    assert page.get_by_label('Wait in seconds', exact=True).input_value() == '60'
    page.get_by_label('Wait before this step').select_option('default')
    page.get_by_role('button', name='Save draft', exact=True).click()
    assert 'delay_before_seconds' not in next(step for step in saved_definition(page)['steps'] if step['id'] == 'run')
    page.get_by_role('button', name='Open reports.example.test', exact=True).click()
    assert page.get_by_label('Wait before this step').count() == 0
    page.get_by_role('button', name='Add wait', exact=True).click()
    assert page.get_by_label('Wait before this step').count() == 0


def test_general_wait_setting_saves_and_failed_save_preserves_value(playback_editor):
    page = playback_editor
    page.get_by_role('button', name='Flows settings', exact=True).click()
    field = page.get_by_label('Default wait before each action (seconds)', exact=True)
    assert field.input_value() == '10'
    field.fill('20')
    page.evaluate('()=>window.previewFailSettings=true')
    page.get_by_role('button', name='Save settings', exact=True).click()
    page.get_by_text('Settings could not be saved. Try again.', exact=True).wait_for()
    assert field.input_value() == '20'
    assert page.get_by_role('button', name='Save settings', exact=True).is_enabled()
    page.evaluate('()=>window.previewFailSettings=false')
    page.get_by_role('button', name='Save settings', exact=True).click()
    page.get_by_text('Flows settings saved', exact=True).wait_for()
    assert page.evaluate('()=>playbackPreview.settings.recording_wait_seconds') == 20
    page.get_by_role('button', name='Back to recording', exact=True).click()
    page.get_by_role('button', name='Click Setting', exact=True).click()
    assert page.get_by_role('option', name='Use default · 20 seconds', exact=True).count() == 1


def test_completed_clicks_distinguish_confirmed_controls_and_advanced_stays_closed(playback_editor):
    page = playback_editor
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.get_by_text('Test passed. Back to Edit Flow, then Save.', exact=True).wait_for()
    assert page.locator('[data-card="setting"] .recording-outcome').inner_text() == 'Setting opened.'
    assert page.locator('[data-card="public"] .recording-outcome').inner_text() == 'Public selected.'
    assert page.locator('[data-card="run"] .recording-outcome').inner_text() == 'Click sent.'
    page.evaluate('''()=>{const session=playbackPreview.data.sessions[0];session.status='failed';session.error='Setting did not open.';
        session.progress_json=JSON.stringify({step_outcomes:{setting:{outcome:'failed',message:'Setting did not open.'}}});}''')
    page.wait_for_function("()=>document.querySelector('[data-card=setting]').dataset.outcome==='failed'")
    page.get_by_role('button', name='Click Setting', exact=True).click()
    assert page.locator('[data-step-failure]').inner_text() == 'Setting did not open.'
    assert not page.locator('[data-section="advanced"]').evaluate('(node)=>node.open')
    assert not page.locator('.recording-locator').is_visible()


def test_debug_log_retry_copy_fallback_pinning_and_edits_preserved(playback_editor):
    page = playback_editor
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.get_by_role('button', name='Debug log', exact=True).wait_for()
    page.get_by_role('button', name='Click Setting', exact=True).click()
    page.get_by_label('Step name', exact=True).fill('Open settings')
    page.evaluate('()=>window.previewFailDebug=true')
    page.get_by_role('button', name='Debug log', exact=True).click()
    page.get_by_text('Debug log is temporarily unavailable.', exact=True).wait_for()
    assert page.get_by_label('Step name', exact=True).input_value() == 'Open settings'
    page.evaluate('()=>window.previewFailDebug=false')
    page.get_by_role('button', name='Retry loading log', exact=True).click()
    page.get_by_label('Debug log text', exact=True).wait_for()
    original = page.get_by_label('Debug log text', exact=True).input_value()
    assert original.startswith('Recording test 2\n')
    assert page.evaluate("()=>playbackPreview.debugRequests[0].headers['X-Client-Key']") == 'fictional-preview'
    page.evaluate("()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Denied')}}})")
    page.get_by_role('button', name='Copy debug log', exact=True).click()
    page.get_by_text('Copy unavailable. The log is selected; copy it with your keyboard.', exact=True).wait_for()
    assert page.get_by_label('Debug log text', exact=True).evaluate('(node)=>node.selectionEnd-node.selectionStart') == len(original)
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.wait_for_function('()=>playbackPreview.data.sessions.length===2')
    assert page.get_by_label('Debug log text', exact=True).input_value() == original
    assert page.locator('[data-debug-title]').inner_text() == 'Debug log · 2'
    page.get_by_role('button', name='Debug log', exact=True).click()
    page.wait_for_function("()=>document.querySelector('[data-debug-text]').value.startsWith('Recording test 4\\n')")
    page.evaluate("()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copiedDebug=text}})")
    page.get_by_role('button', name='Copy debug log', exact=True).click()
    page.get_by_text('Debug log copied.', exact=True).wait_for()
    assert page.evaluate('()=>window.copiedDebug') == page.get_by_label('Debug log text', exact=True).input_value()
    assert page.get_by_label('Step name', exact=True).input_value() == 'Open settings'


def test_debug_late_response_after_close_is_ignored(playback_editor):
    page = playback_editor
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.evaluate('()=>window.previewDeferDebug=true')
    page.get_by_role('button', name='Debug log', exact=True).click()
    page.get_by_text('Loading debug log…', exact=True).wait_for()
    page.get_by_role('button', name='Close debug log', exact=True).click()
    page.evaluate("()=>finishPreviewDebug(new Response('late log',{headers:{'Content-Type':'text/plain'}}))")
    assert page.locator('[data-debug-panel]').is_hidden()
    assert page.locator('[data-debug-text]').input_value() == ''


def test_wait_progress_is_visible_for_download_child_and_narrow_layout(playback_editor):
    page = playback_editor
    page.get_by_role('button', name='Test recording', exact=True).click()
    message = 'Waiting 10 seconds before Click Download Excel.'
    progress = {'message': message, 'step_outcomes': {'download': {'outcome': 'started'}, 'export': {'outcome': 'running', 'remaining_seconds': 10, 'effective_wait_seconds': 10, 'message': message}}}
    page.evaluate('progress=>{const session=playbackPreview.data.sessions[0];session.status="running";session.progress_json=progress;}', json.dumps(progress))
    page.wait_for_function("()=>document.querySelector('[data-card=download] .recording-outcome').textContent.includes('Waiting 10 seconds')")
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('[data-card="download"] .recording-outcome').is_visible()
    assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
    page.get_by_role('button', name='Debug log', exact=True).click()
    page.get_by_label('Debug log text', exact=True).wait_for()
    assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
