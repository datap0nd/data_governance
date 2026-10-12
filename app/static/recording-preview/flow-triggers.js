// Fictional, in-memory preview of several schedule triggers on one Flow. The
// builder and the Flows list are rendered by production app.js; only the API
// layer is replaced. No worker, Outlook, Python or SQL access happens here.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js?v=2', '/static/app.js?v=81', '/static/flow_recording_editor.js?v=13', '/static/flow_recordings.js?v=4']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const status=message=>{document.getElementById('preview-status').textContent=message;};
const emailOff={enabled:false,recipients:[],subject:null};
const data={
    catalog:{sites:[{id:3,name:'Outlook',adapter:'outlook_attachment',enabled:true}],reports:[],asap_download_types:[]},
    sqlCatalog:{configured:true,targets:[{database:'analytics',schema:'feedback',table:'survey_responses'}],scan:{last_scan_at:'2026-10-11T22:00:00Z',duration_ms:2100},missing:[]},
    people:[{id:1,name:'Ops analyst',role:'Analyst',email:'ops@example.test',sql_username:'ops_owner'}],
    flows:[],
};
function common(id,name,extra){
    return {id,name,execution_method:'catalog',classification:'production',flow_folder:`Outlook/${name}`,folder_relative:`Outlook/${name}`,target_folder:null,output_mode:'run_folders',
        period_strategy:'none',download_mode:'single',browser_mode:'headless',selections:{},export_views:[],enabled:true,owner_person_id:1,owner_name:'Ops analyst',
        transform_enabled:false,transform_script_path:null,sql_handoff_enabled:false,sql_mode:null,sql_database:null,sql_schema:null,sql_table:null,sql_target_link_status:null,sql_target_source_id:null,
        post_sql_refresh:{mode:'off',views:[]},email_delivery:{...emailOff},recording_revision_id:null,last_run_at:'2026-10-12T07:00:04Z',last_status:'succeeded',...extra};
}
function reset(){
    data.flows=[
        common(31,'Customer feedback files',{source_type:'outlook',source_adapter:'outlook_attachment',site_id:3,site_name:'Outlook',report_id:9,outlook_subject_contains:'Weekly feedback export',file_format:'auto',filename_template:null,
            schedule_type:'weekly',schedule_time:'08:00',schedule_days:['monday','tuesday','wednesday','thursday','friday'],schedule_day:null,
            additional_triggers:[{type:'weekly',time:'11:00',days:['monday','tuesday','wednesday','thursday','friday'],day:null},{type:'weekly',time:'14:00',days:['monday','tuesday','wednesday','thursday','friday'],day:null}]}),
        common(32,'Stock check scripts',{source_type:'python',source_adapter:'python_script',site_id:null,report_id:null,site_name:'Python',python_scripts:['C:\\Metronome\\Flows\\Python\\check_stock.py'],python_script_arguments:[''],python_script_values:[[]],python_run_mode:'run',python_timeout_minutes:30,python_interpreter:null,
            flow_folder:'Python/Stock check scripts',folder_relative:'Python/Stock check scripts',filename_template:'{flow}.csv',file_format:'csv',schedule_type:'daily',schedule_time:'06:30',schedule_days:[],schedule_day:null,additional_triggers:[]}),
    ];
    window._flowsState={catalog:data.catalog,flows:data.flows,people:data.people,runs:[],scans:[],workers:[{id:'headless-1',status:'online'}],estimates:{},sqlCatalog:data.sqlCatalog,groups:[],view:'builder',runFilter:{},runsPageFull:false};
    window.previewPayload=null;window.previewCalls=[];
}
window.previewData=data;
window._flowWatchExecutionPane=()=>{};window._flowScheduleCatalogMonitor=()=>{};window._flowStopActivityMonitor=()=>{};window._flowRefreshActivity=()=>Promise.resolve();
window.toast=status;
window.navigate=async page=>{if(page==='flows')show('list');};
const record=(method,path,body)=>window.previewCalls.push({method,path,body:body?structuredClone(body):undefined});
const flowId=path=>Number(path.match(/\/api\/flows\/(\d+)/)?.[1]);
const outcome=()=>document.getElementById('preview-outcome').value;
function validationError(body){
    const number=Math.max(2,(body.additional_triggers||[]).length+1);
    const message=`Value error, Trigger ${number} repeats trigger ${number-1}.`;
    return Object.assign(Error(message),{validation:[{loc:['body','additional_triggers'],msg:message}]});
}
function savedFlow(body,id,existing){
    return {...existing,...body,id,owner_name:data.people.find(p=>p.id===body.owner_person_id)?.name||null,additional_triggers:body.schedule_type==='manual'?[]:body.additional_triggers};
}
window.api=async path=>{
    record('GET',path);
    if(path==='/api/flows/sql/catalog')return structuredClone(data.sqlCatalog);
    if(path==='/api/flows')return structuredClone(data.flows);
    if(path.startsWith('/api/flows/runs'))return [];
    if(path==='/api/people')return structuredClone(data.people);
    throw Error('Preview only. No live API requests.');
};
window.apiPostJson=async(path,body)=>{
    record('POST',path,body);
    if(path==='/api/flows'){
        if(outcome()!=='saved')throw validationError(body);
        const id=Math.max(0,...data.flows.map(f=>f.id))+1;
        const created=savedFlow(body,id,common(id,body.name,{source_type:'outlook',source_adapter:'outlook_attachment',site_id:3,site_name:'Outlook',report_id:9}));
        data.flows.push(created);window.previewPayload=body;
        return {...created,standalone:{state:'current'}};
    }
    throw Error('Preview only.');
};
window.apiPut=async(path,body)=>{
    record('PUT',path,body);
    if(outcome()!=='saved')throw validationError(body);
    const id=flowId(path);const index=data.flows.findIndex(f=>f.id===id);if(index<0)throw Error('Flow not found.');
    data.flows[index]=savedFlow(body,id,data.flows[index]);window._flowsState.flows=data.flows;window.previewPayload=body;
    return {...data.flows[index],standalone:{state:'current'}};
};
window.apiPatch=async(path,body)=>{record('PATCH',path,body);const target=data.flows.find(f=>f.id===flowId(path));if(target)Object.assign(target,body);return {...target,...body};};
window.apiPost=async path=>{record('POST',path);if(path==='/api/flows/sql/catalog/refresh')return {accepted:true};throw Error('Preview only.');};
window.apiPostForm=async path=>{record('POST',path);throw Error('Preview only.');};
window.apiDelete=async path=>{record('DELETE',path);return {};};
function show(screen){
    document.querySelectorAll('.preview-nav button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.screen===screen)));
    if(screen==='builder')window._flowShowView('builder',data.flows.find(f=>f.id===31));
    else if(screen==='new')window._flowShowView('builder',{_source_type:'outlook'});
    else if(screen==='python')window._flowShowView('builder',data.flows.find(f=>f.id===32));
    else if(screen==='list')window._flowShowView('list');
}
window.previewShow=show;
document.querySelectorAll('.preview-nav button').forEach(b=>b.onclick=()=>show(b.dataset.screen));
document.getElementById('preview-reset').onclick=()=>{reset();status('Preview reset.');show('builder');};
document.getElementById('preview-outcome').onchange=()=>status(outcome()==='saved'?'The next save succeeds.':'The next save fails validation: the last trigger repeats the one before it.');
reset();
show('builder');
