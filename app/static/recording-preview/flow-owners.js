// Fictional, in-memory preview of Flow ownership. The Flows list and builders
// are rendered by production app.js; only the API layer and the signed-in
// viewer are replaced. No worker, Outlook, SQL or settings file is touched.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js?v=3', '/static/app.js?v=82', '/static/flow_recording_editor.js?v=13', '/static/flow_recordings.js?v=4']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const status=message=>{document.getElementById('preview-status').textContent=message;};
const emailOff={enabled:false,recipients:[],subject:null};
const viewers={
    host:{person_id:null,name:null,is_host:true,is_local:true},
    'host-maya':{person_id:1,name:'Maya Chen',is_host:true,is_local:true},
    omar:{person_id:2,name:'Omar Malik',is_host:false,is_local:false},
};
const data={
    catalog:{sites:[{id:3,name:'Outlook',adapter:'outlook_attachment',enabled:true}],reports:[],asap_download_types:[]},
    sqlCatalog:{configured:true,targets:[{database:'analytics',schema:'sales',table:'regional_orders'}],scan:{last_scan_at:'2026-10-18T22:00:00Z',duration_ms:2100},missing:[]},
    people:[
        {id:1,name:'Maya Chen',role:'BI',email:'maya@example.test',sql_username:'maya_sql',sql_password_set:true,signed_in_computers:1},
        {id:2,name:'Omar Malik',role:'Business',email:'omar@example.test',sql_username:'omar_sql',sql_password_set:false,signed_in_computers:1},
        {id:3,name:'Sofia Rivera',role:'BI',email:null,sql_username:null,sql_password_set:false,signed_in_computers:0},
    ],
    flows:[],
};
function outlookFlow(id,name,owner,extra={}){
    const person=data.people.find(p=>p.id===owner);
    return {id,name,execution_method:'catalog',classification:'production',source_type:'outlook',source_adapter:'outlook_attachment',site_id:3,site_name:'Outlook',report_id:9,
        flow_folder:`Outlook/${name}`,folder_relative:`Outlook/${name}`,target_folder:null,output_mode:'run_folders',outlook_subject_contains:`${name} export`,file_format:'auto',filename_template:null,
        period_strategy:'none',download_mode:'single',browser_mode:'headless',selections:{},export_views:[],enabled:true,owner_person_id:owner,owner_name:person?.name||null,owner_email:person?.email||null,owner_sql_username:person?.sql_username||null,
        transform_enabled:false,transform_script_path:null,sql_handoff_enabled:true,sql_assign_owner:true,sql_mode:'append',sql_database:'analytics',sql_schema:'sales',sql_table:'regional_orders',sql_target_link_status:null,sql_target_source_id:null,
        post_sql_refresh:{mode:'off',views:[]},email_delivery:{...emailOff},recording_revision_id:null,schedule_type:'daily',schedule_time:'08:00',schedule_days:[],schedule_day:null,additional_triggers:[],
        last_run_at:'2026-10-19T04:00:04Z',last_status:'succeeded',...extra};
}
function reset(){
    data.flows=[
        outlookFlow(41,'Regional orders',1),
        outlookFlow(42,'Customer feedback files',2,{schedule_time:'09:00'}),
        outlookFlow(43,'Legacy stock check',null,{sql_handoff_enabled:false}),
        outlookFlow(44,'Daily returns',null,{last_status:'failed'}),
    ];
    window._flowsState={catalog:data.catalog,flows:data.flows,people:data.people,runs:[],scans:[],workers:[{id:'headless-1',status:'online'}],estimates:{},sqlCatalog:data.sqlCatalog,groups:[],view:'list',classification:'production',runFilter:{},runsPageFull:false};
    window._flowOwnerFilterMemory='all';
    window.previewPayload=null;window.previewCalls=[];
}
window.previewData=data;
window._flowWatchExecutionPane=()=>{};window._flowScheduleCatalogMonitor=()=>{};window._flowStopActivityMonitor=()=>{};window._flowRefreshActivity=()=>Promise.resolve();
window.toast=status;
window.navigate=async page=>{if(page==='flows')show('list');};
const record=(method,path,body)=>window.previewCalls.push({method,path,body:body?structuredClone(body):undefined});
const flowId=path=>Number(path.match(/\/api\/flows\/(\d+)/)?.[1]);
const me=()=>window._currentUser;
function forbidden(flow){
    // The server's rule, mirrored for the fictional API.
    if(me().is_host||(flow.owner_person_id&&flow.owner_person_id===me().person_id))return null;
    return Object.assign(Error(flow.owner_name?`This Flow belongs to ${flow.owner_name}. You can view and run it; only ${flow.owner_name} or the host PC can change it.`:'This Flow has no owner. Only the host PC can change it or assign it to someone.'),{status:403});
}
function withOwner(flow,ownerId){
    const person=data.people.find(p=>p.id===ownerId);
    return Object.assign(flow,{owner_person_id:ownerId||null,owner_name:person?.name||null,owner_email:person?.email||null,owner_sql_username:person?.sql_username||null});
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
        if(!me().is_host&&body.owner_person_id&&body.owner_person_id!==me().person_id)throw Error('Only the host PC can create a Flow for someone else.');
        const id=Math.max(0,...data.flows.map(f=>f.id))+1;
        const created=withOwner({...outlookFlow(id,body.name,null),...body,id},me().is_host?body.owner_person_id:me().person_id);
        data.flows.push(created);window.previewPayload=body;
        status(`${created.name} created for ${created.owner_name||'nobody (unassigned)'}.`);
        return {...created,standalone:{state:'current'}};
    }
    throw Error('Preview only.');
};
window.apiPut=async(path,body)=>{
    record('PUT',path,body);
    const flow=data.flows.find(f=>f.id===flowId(path));if(!flow)throw Error('Flow not found.');
    const denied=forbidden(flow);if(denied)throw denied;
    const owner=me().is_host?body.owner_person_id:flow.owner_person_id;
    Object.assign(flow,body);withOwner(flow,owner);window.previewPayload=body;
    return {...flow,standalone:{state:'current'}};
};
window.apiPatch=async(path,body)=>{
    record('PATCH',path,body);
    const flow=data.flows.find(f=>f.id===flowId(path));if(!flow)throw Error('Flow not found.');
    const denied=forbidden(flow);if(denied)throw denied;
    if('owner_person_id' in body){
        if(!me().is_host)throw Error('Only the host PC can assign a Flow to someone else.');
        const previous=flow.owner_name;withOwner(flow,body.owner_person_id);
        status(previous&&previous!==flow.owner_name?`${flow.name} now belongs to ${flow.owner_name||'nobody'}. ${previous} is emailed about the change.`:`${flow.name} now belongs to ${flow.owner_name||'nobody'}.`);
        return {id:flow.id,owner_person_id:flow.owner_person_id};
    }
    Object.assign(flow,body);return {id:flow.id,...body};
};
window.apiPost=async path=>{record('POST',path);if(/\/run$/.test(path)){status('Run queued (fictional).');return {id:900,status:'queued'};}throw Error('Preview only.');};
window.apiPostForm=async path=>{record('POST',path);throw Error('Preview only.');};
window.apiDelete=async path=>{record('DELETE',path);return {};};
function setViewer(key){
    window._currentUser={...viewers[key],ip:key==='omar'?'203.0.113.9':'127.0.0.1',is_admin:true};
}
function show(screen){
    document.querySelectorAll('.preview-nav button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.screen===screen)));
    if(screen==='maya')window._flowShowView('builder',data.flows.find(f=>f.id===41));
    else if(screen==='new')window._flowShowView('builder',{_source_type:'outlook'});
    else window._flowShowView('list');
}
window.previewShow=show;
document.querySelectorAll('.preview-nav button').forEach(b=>b.onclick=()=>show(b.dataset.screen));
document.getElementById('preview-viewer').onchange=event=>{setViewer(event.target.value);status(`Now viewing as ${event.target.selectedOptions[0].textContent}.`);show(document.querySelector('.preview-nav [aria-current=true]').dataset.screen);};
document.getElementById('preview-reset').onclick=()=>{reset();document.getElementById('preview-viewer').value='host';setViewer('host');status('Preview reset.');show('list');};
reset();
setViewer('host');
show('list');
