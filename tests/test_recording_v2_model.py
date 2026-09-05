import copy
import pytest
from app import flow_recording as model
from app import flow_recording_runtime as runtime
from test_flow_recordings import definition


@pytest.mark.parametrize('seconds',[0,601,True,1.5,'5'])
def test_wait_rejects_invalid_duration(seconds):
    value=definition(); value['steps'].insert(1,dict(id='wait',action='wait',page='page',seconds=seconds))
    with pytest.raises(ValueError,match='seconds'):
        model.validate_definition(value)


def test_wait_roundtrip_and_v1_reader():
    value=definition(); value['version']=1
    model.validate_definition(value)
    value['steps'].insert(1,dict(id='wait',action='wait',page='page',seconds=5,label='Let the portal settle'))
    with pytest.raises(ValueError): model.validate_definition(value)
    value['version']=2
    assert model.validate_definition(value)['steps'][1]['label']=='Let the portal settle'


def test_page_dependency_rejects_use_before_popup_or_after_close():
    value=definition()
    popup=dict(id='popup',action='popup',page='page',result_page='page1',steps=[dict(id='trigger',action='click',page='page',locator=[dict(method='get_by_text',args=['Open'],kwargs={'exact':True})])])
    use=dict(id='use',action='goto',page='page1',args=['https://example.test'])
    value['steps'].extend([popup,use])
    model.validate_definition(value,activation=False)
    value['steps'][-2:]=[use,popup]
    with pytest.raises(ValueError,match='page'): model.validate_definition(value,activation=False)
    value['steps'][-2:]=[popup,dict(id='close',action='close',page='page1'),use]
    with pytest.raises(ValueError,match='page'): model.validate_definition(value,activation=False)


def test_identity_candidates_keep_frame_and_avoid_generic_status():
    value=definition(); value['identity']={}
    assert model.suggest_review(value)['identity']=={}  # Ready is a status, not a report title.
    locator=[dict(method='frame_locator',args=['iframe'],kwargs={}),dict(method='get_by_role',args=['heading'],kwargs={'name':'Sales Report','exact':True})]
    assertion=dict(id='title',action='assert',assertion='to_be_visible',page='page',locator=locator,args=[])
    value['steps'].insert(2,assertion)
    proposed=model.suggest_review(value)
    assert proposed['identity']['text']=='Sales Report'
    assert proposed['identity']['target']=={'page':'page','locator':locator}
    assert value['identity']=={}
    second=copy.deepcopy(assertion); second['id']='title2'; second['locator'][-1]['kwargs']['name']='Another Report'
    value['steps'].insert(3,second)
    assert not model.suggest_review(value)['identity']


def test_wait_cancellation_stops_before_browser_interaction(tmp_path):
    value=definition(); value['steps'].insert(0,dict(id='wait',action='wait',page='page',seconds=600))
    job={'recording':{'definition':value,'revision':1},'recording_parameters':model.resolve_parameters(value)}
    events=[]
    def progress(status,detail,*args):
        events.append(detail)
        if detail['outcome']=='running': raise RuntimeError('Cancelled by user')
    with pytest.raises(RuntimeError,match='Cancelled'):
        runtime.acquire(object(),job,progress,tmp_path,tmp_path/'staging',target=tmp_path/'output',run_id=1,artifacts=[])
    assert [e['outcome'] for e in events]==['started','running','failed']
