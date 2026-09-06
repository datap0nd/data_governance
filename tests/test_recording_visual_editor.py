import copy
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from test_flow_recordings import definition
from app.flow_recording import validate_definition


@pytest.fixture
def editor_page():
    root=Path(__file__).resolve().parents[1]
    value=definition()
    download=next(s for s in value['steps'] if s['action']=='download')
    download['steps'][0]['locator'][-1]['kwargs']['name']='Excel down'
    data={'flow':{'id':1,'name':'Sales report','source_adapter':'gscm_portal','schedule_type':'daily','enabled':False},'sessions':[],
          'revisions':[{'id':1,'status':'draft','definition':value}]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1400,'height':1000})
        page.set_content('<main>Visual recording fixture</main>')
        page.add_style_tag(path=str(root/'app/static/style.css'))
        page.evaluate('''data=>{window.data=data;window.calls=[];
            window.api=async()=>structuredClone(data);
            window.apiPostJson=async(path,body)=>{calls.push({path,body});if(path.endsWith('/validate')){data.revisions[0].status='validated';return {};}const id=data.revisions.length+1;data.revisions.unshift({id,status:'draft',definition:structuredClone(body.definition)});return {revision_id:id};};
            window.apiPost=async path=>{calls.push({path});if(path.endsWith('/validate'))data.revisions[0].status='validated';if(path.endsWith('/activate'))data.flow.recording_revision_id=data.revisions[0].id;return {};};
            window.apiPatch=async(path,body)=>{calls.push({path,body});data.flow.enabled=body.enabled;return data.flow;};
        }''',data)
        for script in ('flow_recording_model.js','flow_recording_editor.js','flow_recordings.js'):
            page.add_script_tag(path=str(root/'app/static'/script))
        page.evaluate('()=>FlowRecordings.open(1)')
        yield page,value
        browser.close()


def test_one_download_card_and_consistent_options(editor_page):
    page,value=editor_page
    assert page.locator('[data-card]').count()==len(value['steps'])
    page.get_by_role('button',name='Click “Excel down”',exact=True).click()
    assert page.locator('.recording-badge').count()==1
    assert page.get_by_label('This action produces a download').is_checked()
    assert page.get_by_label('Output format').is_visible()
    assert not page.locator('[data-headers]').is_visible()
    assert page.locator('.recording-details').count()==1
    page.get_by_role('button',name='Save draft',exact=True).click()
    saved=page.evaluate('()=>calls[0].body.definition')
    validate_definition(saved)
    assert saved['steps'][-1]['steps'][0]['id']==value['steps'][-1]['steps'][0]['id']
    assert not page.get_by_text('Revision 1',exact=False).count()


def test_wait_undo_move_and_remove_preserve_ids(editor_page):
    page,value=editor_page
    original_ids=[s['id'] for s in value['steps']]
    value['steps'].insert(1,{'id':'legacy-wait','page':'page','action':'wait','seconds':5})
    page.locator('[data-close]').click()
    page.evaluate('v=>data.revisions[0].definition=v',value)
    page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/static/flow_recording_editor.js'))
    page.evaluate('()=>FlowRecordings.open(1)')
    page.locator('[data-select="legacy-wait"]').click()
    page.get_by_label('Wait in seconds').fill('8');page.get_by_label('Wait in seconds').press('Tab')
    wait_id=page.locator('[data-card].selected').get_attribute('data-card')
    page.get_by_role('button',name='Move down',exact=True).click()
    assert page.locator('[data-card]').nth(2).get_attribute('data-card')==wait_id
    page.get_by_role('button',name='Undo',exact=True).click()
    assert page.locator('[data-card]').nth(1).get_attribute('data-card')==wait_id
    page.get_by_role('button',name='Remove',exact=True).click()
    assert page.locator('[data-card]').count()==len(original_ids)
    page.get_by_role('button',name='Undo',exact=True).click()
    page.get_by_role('button',name='Save draft',exact=True).click()
    saved=page.evaluate('()=>calls[0].body.definition')
    assert saved['steps'][1]['id']==wait_id and saved['steps'][1]['seconds']==8
    assert [s['id'] for s in saved['steps'] if s['action']!='wait']==original_ids


def test_polling_preserves_selection_dirty_fields_and_collapsed_details(editor_page):
    page,value=editor_page
    page.get_by_role('button',name='Click “Excel down”',exact=True).click()
    page.get_by_label('Step name',exact=True).fill('Download sales workbook')
    page.evaluate('()=>window.selectedPanel=document.querySelector(".recording-details")')
    page.wait_for_timeout(3300)
    assert page.get_by_label('Step name',exact=True).input_value()=='Download sales workbook'
    assert page.evaluate('()=>selectedPanel===document.querySelector(".recording-details")')
    assert not page.locator('[data-headers]').is_visible()
    assert page.get_by_role('button',name='Enable schedule',exact=True).count()==0
    page.get_by_role('button',name='Test recording',exact=True).click()
    page.wait_for_function('()=>calls.length>=2')
    assert page.evaluate('()=>calls.map(c=>c.path)')==['/api/flows/1/recordings/revisions','/api/flows/1/recordings/revisions/2/validate']
    assert page.get_by_role('button',name='Enable schedule',exact=True).count()==0
    page.get_by_label('Step name',exact=True).fill('Changed after testing')
    assert page.get_by_role('button',name='Enable schedule',exact=True).count()==0


def test_testing_does_not_activate_or_enable_schedule(editor_page):
    page,_=editor_page
    page.get_by_role('button',name='Test recording',exact=True).click()
    page.wait_for_function('()=>calls.length===2')
    assert page.evaluate('()=>data.flow.enabled') is False
    assert all('/activate' not in path for path in page.evaluate('()=>calls.map(c=>c.path)'))


def test_nested_progress_maps_to_single_card_and_failure_explanation(editor_page):
    page,value=editor_page
    event=next(s for s in value['steps'] if s['action']=='download');trigger=event['steps'][0]
    progress={'step_id':trigger['id'],'step_outcomes':{event['id']:{'outcome':'started'},trigger['id']:{'outcome':'completed'}}}
    page.evaluate('p=>data.sessions=[{revision_id:1,scan_id:3,operation:"validate",status:"running",progress_json:JSON.stringify(p)}]',progress)
    page.wait_for_function('()=>document.querySelector("[data-outcome=running]")')
    assert page.locator('[data-outcome=completed]').count()==0
    page.get_by_role('button',name='Click “Excel down”',exact=True).click()
    progress['step_outcomes'][event['id']]={'outcome':'failed','message':'Download did not complete'}
    page.evaluate('p=>{data.sessions[0].status="failed";data.sessions[0].progress_json=JSON.stringify(p);}',progress)
    page.wait_for_function('()=>document.querySelector("[data-outcome=failed]")')
    assert page.locator('[data-step-failure]').inner_text()=='Download did not complete'


def test_mobile_details_follow_selected_card_and_keyboard_selection(editor_page):
    page,_=editor_page
    page.set_viewport_size({'width':600,'height':900})
    assert page.locator('.recording-details').is_hidden()
    button=page.get_by_role('button',name='Click “Excel down”',exact=True)
    button.focus();page.keyboard.press('Enter')
    assert page.locator('[data-card].selected + .recording-details').count()==1
    page.get_by_role('button',name='Move up',exact=True).focus()
    # Recorded actions can be reordered while their page remains available.
    page.keyboard.press('Enter')
    assert page.locator('.recording-details').count()==1


def test_compound_download_stays_grouped_and_roundtrips(editor_page):
    page,value=editor_page
    event=next(s for s in value['steps'] if s['action']=='download')
    extra=copy.deepcopy(event['steps'][0]);extra['id']='prepare-export';extra['locator'][-1]['kwargs']['name']='Prepare Excel'
    event['steps'].insert(0,extra)
    page.locator('[data-close]').click()
    page.evaluate('value=>{data.revisions[0].definition=value;}',value)
    page.evaluate('()=>FlowRecordings.open(1)')
    page.get_by_role('button',name='Download files',exact=True).click()
    page.get_by_text('2 actions in this event group',exact=True).click()
    page.get_by_role('button',name='Click “Excel down”',exact=True).click()
    assert page.get_by_label('This action produces a download').is_checked()
    assert page.get_by_label('This action produces a download').is_disabled()
    page.get_by_role('button',name='Edit download group',exact=True).click()
    page.get_by_role('button',name='Move up',exact=True).click()
    page.get_by_role('button',name='Save draft',exact=True).click()
    saved=page.evaluate('()=>calls[0].body.definition')
    validate_definition(saved)
    assert next(s for s in saved['steps'] if s['id']==event['id'])==event


def test_missing_identity_does_not_block_test_and_download_is_not_label_inferred(editor_page):
    page,value=editor_page
    event=value['steps'][-1];value['steps'][-1]=event['steps'][0]
    value['identity']={}
    target={'page':'page','locator':[{'method':'frame_locator','args':['iframe'],'kwargs':{}},{'method':'get_by_text','args':['Sales Report'],'kwargs':{'exact':True}}]}
    value['identity_candidates']=[{'text':'Sales Report','kind':'element','target':target,'step_id':'title'}]
    page.locator('[data-close]').click();page.evaluate('v=>data.revisions[0].definition=v',value);page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/static/flow_recording_editor.js'));page.evaluate('()=>FlowRecordings.open(1)')
    page.get_by_role('button',name='Click “Excel down”',exact=True).click()
    assert not page.get_by_label('This action produces a download').is_checked()
    page.get_by_label('This action produces a download').check()
    page.get_by_role('button',name='Test recording',exact=True).click()
    page.wait_for_function('()=>calls.length===2')
    assert page.get_by_text('Check the recorded page',exact=True).count()==0
    saved=page.evaluate('()=>calls[0].body.definition')
    assert saved['identity']=={}
    assert saved['identity_candidates'][0]['target']==target
    assert saved['steps'][-1]['steps'][0]['id']==event['steps'][0]['id']


def test_drag_and_invalid_page_move(editor_page):
    page,value=editor_page
    original_ids=[s['id'] for s in value['steps']]
    value['steps'].insert(1,{'id':'legacy-wait','page':'page','action':'wait','seconds':5})
    page.locator('[data-close]').click()
    page.evaluate('v=>data.revisions[0].definition=v',value)
    page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/static/flow_recording_editor.js'))
    page.evaluate('()=>FlowRecordings.open(1)')
    page.locator('[data-select="legacy-wait"]').click()
    wait_id=page.locator('[data-card].selected').get_attribute('data-card')
    page.get_by_role('button',name='Move down',exact=True).click()
    page.get_by_role('button',name='Move down',exact=True).click()
    assert page.locator('[data-card]').nth(3).get_attribute('data-card')==wait_id
    popup={'id':'popup','action':'popup','page':'page','result_page':'page1','steps':[{'id':'popup-click','action':'click','page':'page','locator':[{'method':'get_by_text','args':['Open export'],'kwargs':{'exact':True}}]}]}
    use={'id':'popup-use','action':'goto','page':'page1','args':['https://example.test/export']}
    value['steps'].extend([popup,use])
    page.locator('[data-close]').click();page.evaluate('v=>data.revisions[0].definition=v',value);page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/static/flow_recording_editor.js'));page.evaluate('()=>FlowRecordings.open(1)')
    page.locator('[data-select="popup-use"]').click();page.get_by_role('button',name='Move up',exact=True).click()
    assert 'before it opens' in page.locator('[data-error]').inner_text()
    assert page.locator('[data-card]').last.get_attribute('data-card')=='popup-use'

def test_failed_save_keeps_edits_and_places_error_by_actions(editor_page):
    page,_=editor_page
    page.get_by_role('button',name='Click “Excel down”',exact=True).click()
    page.get_by_label('Step name',exact=True).fill('My pending download')
    page.evaluate("()=>apiPostJson=async()=>{throw Error('Connection lost. Retry Save draft.');}")
    page.get_by_role('button',name='Save draft',exact=True).click()
    page.get_by_text('Connection lost. Retry Save draft.',exact=True).wait_for()
    assert page.get_by_label('Step name',exact=True).input_value()=='My pending download'
    assert page.locator('[data-error]').bounding_box()['y']<page.viewport_size['height']
    assert page.get_by_role('button',name='Save draft',exact=True).is_enabled()


def test_leaving_during_save_does_not_launch_a_late_test(editor_page):
    page,_=editor_page
    page.evaluate("()=>apiPostJson=(path,body)=>new Promise(resolve=>{window.finishSave=resolve;calls.push({path,body});})")
    page.get_by_role('button',name='Test recording',exact=True).click()
    page.get_by_role('button',name='Back to Edit Flow',exact=True).click()
    page.evaluate('()=>finishSave({revision_id:2})')
    page.wait_for_timeout(100)
    assert page.evaluate('()=>calls.length')==1
    assert page.locator('.flow-recording-page').count()==0

def test_opening_history_clears_previous_pending_selection(editor_page):
    page,_=editor_page
    page.locator('[data-close]').click()
    page.evaluate('''()=>{data.revisions.push({...structuredClone(data.revisions[0]),id:2});
      window._flowRecordingSelections=new Map([[1,1]]);window.accepted=null;
      window._flowAcceptRecording=(id,r)=>window.accepted=r;}''')
    page.evaluate('()=>FlowRecordings.open(1,{recording_revision_id:1})')
    page.get_by_text('More',exact=True).click()
    page.get_by_role('button',name='Saved versions',exact=True).click()
    page.locator('[data-version="2"]').click()
    page.locator('[data-close]').click()
    assert page.evaluate('()=>window._flowRecordingSelections.get(1)') is None
    assert page.evaluate('()=>window.accepted') is None


def test_opening_recording_preserves_setup_for_tab_navigation(editor_page):
    page,_=editor_page
    page.locator('[data-close]').click()
    page.evaluate('()=>window._flowBuilderDrafts=new Map()')
    page.evaluate("()=>FlowRecordings.open(1,{name:'Pending setup',filename_template:'pending.csv'})")
    # Tab navigation removes the recording page instead of calling Back.
    page.evaluate("()=>document.querySelector('.flow-recording-page').remove()")
    assert page.evaluate('()=>window._flowBuilderDrafts.get(1)')=={'name':'Pending setup','filename_template':'pending.csv'}
