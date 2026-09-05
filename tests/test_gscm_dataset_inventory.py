import pytest
from playwright.sync_api import sync_playwright

from app import flow_gscm as gscm


@pytest.fixture
def dataset_page():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.set_content('<p>Dataset fixture</p>')
        page.evaluate('''() => {
            window.rows=Array.from({length:350},(_,i)=>({userreportid:'ID'+i,userreportname:'Report '+i,
                publicscope:'Private',scope:'AS',gbm:'MX'}));
            window.filtered=rows.slice(10,15);
            window.ds={filterstr:'hidden rows',getRowCount:()=>filtered.length,
                getRowCountNF:()=>rows.length,getColumn:(r,c)=>filtered[r][c],getColumnNF:(r,c)=>rows[r][c],
                addEventHandler:(name,handler)=>window.loaded=()=>handler(ds,{errorcode:0})};
            window.nexacro={getApplication:()=>({gds_bookmark:ds})};
        }''')
        try: yield page
        finally: browser.close()


def test_350_bookmarks_are_read_including_filtered_out_rows_without_grid_enumeration(dataset_page,monkeypatch):
    page=dataset_page
    inventory=page.evaluate(gscm._BOOKMARK_DATASET_JS,list(gscm.BOOKMARK_DATASET_COLUMNS))
    assert inventory['filtered_count']==5 and inventory['total_count']==350
    assert inventory['rows'][0]['userreportid']=='ID0'
    assert inventory['rows'][-1]['userreportid']=='ID349'
    monkeypatch.setattr(gscm,'select_scope_tab',lambda p,*a,**k: p.evaluate('() => { loaded(); return true; }'))
    monkeypatch.setattr(gscm,'wait_window_visible',lambda _:False)
    entries=gscm._dataset_tab_bookmarks(page,'Private',lambda _:None)
    assert len(entries)==350
    assert entries[175]['bookmark_id']=='ID175'
    assert entries[0]['module']=='MX'


def test_unchanged_nonempty_count_never_certifies_a_scope(dataset_page,monkeypatch):
    page=dataset_page
    monkeypatch.setattr(gscm,'select_scope_tab',lambda *a,**k:True)
    monkeypatch.setattr(gscm,'wait_window_visible',lambda _:False)
    monkeypatch.setattr(page,'wait_for_timeout',lambda _:None)
    messages=[]
    assert gscm._dataset_tab_bookmarks(page,'Private',messages.append) is None
    assert 'scope_load_unproven' in messages[-1]


def test_empty_scope_requires_a_load_event_and_current_snapshot(dataset_page,monkeypatch):
    page=dataset_page
    monkeypatch.setattr(gscm,'select_scope_tab',lambda p,*a,**k: p.evaluate('() => { rows=[]; filtered=[]; loaded(); return true; }'))
    monkeypatch.setattr(gscm,'wait_window_visible',lambda _:False)
    assert gscm._dataset_tab_bookmarks(page,'Public',lambda _:None)==[]
    assert page.evaluate(gscm._BOOKMARK_LOAD_JS,False)['loaded']
    page.evaluate("() => rows.push({userreportid:'new'})")
    assert not page.evaluate(gscm._BOOKMARK_LOAD_JS,False)['loaded']


def test_filtered_dataset_without_nf_support_remains_incomplete(dataset_page):
    page=dataset_page
    page.evaluate('() => { delete ds.getRowCountNF; delete ds.getColumnNF; }')
    page.evaluate(gscm._BOOKMARK_LOAD_JS,True)
    page.evaluate('() => loaded()')
    assert not page.evaluate(gscm._BOOKMARK_LOAD_JS,False)['loaded']


def test_delayed_load_is_awaited_and_failed_load_is_not_accepted(dataset_page,monkeypatch):
    page=dataset_page
    monkeypatch.setattr(gscm,'select_scope_tab',lambda p,*a,**k: p.evaluate('() => { setTimeout(()=>loaded(),100); return true; }'))
    monkeypatch.setattr(gscm,'wait_window_visible',lambda _:False)
    assert len(gscm._dataset_tab_bookmarks(page,'Private',lambda _:None))==350
    page.evaluate("() => ds.__metronomeBookmarkLoad.error=true")
    monkeypatch.setattr(gscm,'select_scope_tab',lambda *a,**k:True)
    assert gscm._dataset_tab_bookmarks(page,'Private',lambda _:None) is None


def test_old_load_cannot_certify_a_new_scope_while_its_request_is_pending(dataset_page,monkeypatch):
    page=dataset_page
    page.evaluate(gscm._BOOKMARK_LOAD_JS,True)
    page.evaluate('() => loaded()')
    monkeypatch.setattr(gscm,'select_scope_tab',lambda *a,**k:True)
    monkeypatch.setattr(gscm,'wait_window_visible',lambda _:False)
    monkeypatch.setattr(page,'wait_for_timeout',lambda _:None)
    assert gscm._dataset_tab_bookmarks(page,'Public',lambda _:None) is None


def test_direct_selection_resolves_first_middle_last_ids_after_sort_changes(dataset_page):
    page=dataset_page
    page.evaluate("""() => {
        window.bound={getRowCount:()=>rows.length,getColumn:(r,c)=>rows[r][c],rowposition:-1,
            set_rowposition(r){this.rowposition=r; grid.currentrow=r;}};
        window.grid={getBindDataset:()=>bound,visible:true,selecttype:'row',
            selectRow(r){this.selectstartrow=r;this.selectendrow=r;}};
        window.nexacro={getApplication:()=>({mainframe:{Setting0:{form:{grd_bookmark:grid}}}})};
        window.scrollTo=()=>{throw Error('must not scroll')};
    }""")
    for index in [0,175,349]:
        page.evaluate('() => rows.reverse()')
        result=page.evaluate(gscm._SELECT_BOOKMARK_ROW_JS,{'bookmark_id':'ID'+str(index),'bookmark_name':'Report '+str(index),
            'grid_id':'mainframe.Setting0.form.grd_bookmark','grid_suffix':'.grd_bookmark'})
        assert result['selected'],result
        assert page.evaluate('() => bound.getColumn(bound.rowposition,"userreportid")')=='ID'+str(index)
    page.evaluate('() => bound.set_rowposition = function(r) {this.rowposition=r; grid.currentrow=r; rows[r].userreportname="Renamed";}')
    result=page.evaluate(gscm._SELECT_BOOKMARK_ROW_JS,{'bookmark_id':'ID349','bookmark_name':'Report 349',
        'grid_id':'mainframe.Setting0.form.grd_bookmark','grid_suffix':'.grd_bookmark'})
    assert result['selected'] is False
