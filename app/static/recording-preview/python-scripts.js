// Fictional, in-memory preview of Python-script Flows: the source picker, the
// Python builder, the Flows list and run history are rendered by production
// app.js; only the API layer is replaced. No worker, Python, PostgreSQL or file
// access happens here.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js?v=2', '/static/app.js?v=78', '/static/flow_recording_editor.js?v=13', '/static/flow_recordings.js?v=4']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const status=message=>{document.getElementById('preview-status').textContent=message;};
const now=()=>new Date().toISOString();
const SCRIPTS=['C:\\Metronome\\Flows\\Python\\fetch_orders.py','C:\\Metronome\\Flows\\Python\\clean_orders.py'];
const data={
    catalog:{sites:[{id:1,name:'GSCM',adapter:'gscm_portal',enabled:true},{id:2,name:'ASAP',adapter:'asap_portal',enabled:true}],reports:[{id:5,site_id:1,name:'Regional sales',enabled:true,stale:false,automation:{kind:'bookmark'}}],asap_download_types:[{key:'excel',file_format:'xlsx',preferred_suffix:'.xlsx',label:'Excel workbook'}]},
    sqlCatalog:{configured:true,targets:[{database:'analytics',schema:'public',table:'orders_clean'},{database:'analytics',schema:'public',table:'regional_orders'},{database:'analytics',schema:'staging',table:'returns_raw'}],scan:{last_scan_at:now(),duration_ms:3100},missing:[]},
    people:[{id:1,name:'Ops analyst',role:'Analyst',email:'ops@example.test',sql_username:'ops_owner'},{id:2,name:'Planner',role:'Planner',email:'planner@example.test',sql_username:null}],
    views:[{database:'analytics',schema:'bi',name:'orders_by_region_mv',source_name:'bi.orders_by_region_mv'},{database:'analytics',schema:'bi',name:'orders_daily_mv',source_name:'bi.orders_daily_mv'},{database:'analytics',schema:'bi',name:'executive_summary_mv',source_name:'bi.executive_summary_mv'}],
    upstream:{'bi.executive_summary_mv':['bi.orders_by_region_mv']},
    flows:[],runs:[],
};
const emailOff={enabled:false,recipients:[],subject:null};
function pythonFlow(id,name,extra={}){
    return {id,name,source_type:'python',source_adapter:'python_script',site_id:null,report_id:null,site_name:'Python',python_scripts:[...SCRIPTS],execution_method:'catalog',classification:'production',
        flow_folder:`Python/${name}`,folder_relative:`Python/${name}`,target_folder:null,output_mode:'run_folders',filename_template:'{flow}.csv',file_format:'csv',period_strategy:'none',download_mode:'single',browser_mode:'headless',selections:{},export_views:[],
        schedule_type:'daily',schedule_time:'06:30',schedule_days:[],schedule_day:1,enabled:true,owner_person_id:1,owner_name:'Ops analyst',transform_enabled:false,transform_script_path:null,
        sql_handoff_enabled:true,sql_mode:'replace',sql_uppercase:false,sql_database:'analytics',sql_schema:'public',sql_table:'orders_clean',sql_target_link_status:'confirmed',sql_target_source_id:31,
        post_sql_refresh:{mode:'manual',views:[{database:'analytics',schema:'bi',name:'orders_by_region_mv'}]},email_delivery:{...emailOff},recording_revision_id:null,last_run_at:null,last_status:null,...extra};
}
function portalFlow(id,name){
    return {id,name,source_type:'portal',site_id:1,site_name:'GSCM',source_adapter:'gscm_portal',report_id:5,report_name:'Regional sales',execution_method:'catalog',classification:'production',flow_folder:`GSCM/${name} (id ${id})`,folder_relative:`GSCM/${name} (id ${id})`,target_folder:null,output_mode:'direct_replace',filename_template:'orders_{export}.xlsx',period_strategy:'none',file_format:'xlsx',schedule_type:'manual',browser_mode:'headed',selections:{},export_views:[],enabled:false,
        sql_handoff_enabled:false,post_sql_refresh:{mode:'off',views:[]},email_delivery:{...emailOff},recording_revision_id:null,last_run_at:'2026-09-14T04:00:00Z',last_status:'succeeded',owner_person_id:2,owner_name:'Planner'};
}
function pythonJob(flow,extra={}){
    return {flow:{id:flow.id,name:flow.name,execution_method:'catalog',source_type:'python'},execution:{browser_mode:'headless',required_adapter:'python_script'},downloads:{periods:[null],output_mode:flow.output_mode,filename_template:flow.filename_template},report:{export_views:[]},transformation:{enabled:false},
        sql_handoff:{enabled:flow.sql_handoff_enabled,mode:flow.sql_mode,database:flow.sql_database,schema:flow.sql_schema,table:flow.sql_table},post_sql_refresh:flow.post_sql_refresh,
        python_source:{enabled:true,scripts:[...flow.python_scripts],output_format:flow.file_format,destination:flow.sql_handoff_enabled?'sql':'file',timeout_seconds:3600},...extra};
}
function reset(){
    data.flows=[
        pythonFlow(21,'Orders extract',{last_run_at:'2026-09-16T02:30:00Z',last_status:'failed'}),
        pythonFlow(22,'Supplier lead times',{python_scripts:[SCRIPTS[0].replace('fetch_orders','fetch_lead_times')],sql_handoff_enabled:false,file_format:'xlsx',filename_template:'{flow}_{date}.xlsx',output_mode:'direct_replace',schedule_type:'weekly',schedule_days:['monday'],post_sql_refresh:{mode:'off',views:[]},sql_mode:null,sql_database:null,sql_schema:null,sql_table:null,sql_target_link_status:null,sql_target_source_id:null,email_delivery:{enabled:true,recipients:['planner@example.test'],subject:null},last_run_at:'2026-09-15T02:30:00Z',last_status:'succeeded'}),
        portalFlow(7,'Regional orders'),
    ];
    const failed=data.flows[0];
    data.runs=[
        {id:52,flow_id:21,flow_name:'Orders extract',status:'failed',trigger_type:'scheduled',requested_by:null,worker_id:'headless-2',created_at:'2026-09-16T02:30:00Z',started_at:'2026-09-16T02:30:04Z',finished_at:'2026-09-16T02:30:19Z',
            error:"Python script clean_orders.py (step 2 of 2) failed with exit code 1: KeyError 'region'",job:pythonJob(failed),progress:{stage:'failed',message:'Python script failed'},artifacts:[],
            timings:[{phase:'python_scripts',duration_ms:14600,status:'failed'},{phase:'total',duration_ms:15000,status:'failed'}],sql_outcome:null,view_refresh:null,
            events:[{id:1,status:'running',stage:'python_scripts',message:'Running 2 Python script(s) in order: fetch_orders.py → clean_orders.py.',details:{},created_at:'2026-09-16T02:30:04Z'},{id:2,status:'running',stage:'python_step',message:'Running script 1 of 2: fetch_orders.py.',details:{step:1,steps:2},created_at:'2026-09-16T02:30:04Z'},{id:3,status:'running',stage:'python_step',message:'Running script 2 of 2: clean_orders.py.',details:{step:2,steps:2},created_at:'2026-09-16T02:30:12Z'},{id:4,status:'failed',stage:'failed',message:"Python script clean_orders.py (step 2 of 2) failed with exit code 1: KeyError 'region'",details:{},error:"KeyError 'region'",created_at:'2026-09-16T02:30:19Z'}],files:[],downloads:null,email:null},
        {id:51,flow_id:21,flow_name:'Orders extract',status:'succeeded',trigger_type:'scheduled',requested_by:null,worker_id:'headless-2',created_at:'2026-09-15T02:30:00Z',started_at:'2026-09-15T02:30:03Z',finished_at:'2026-09-15T02:31:10Z',error:null,job:pythonJob(failed),
            progress:{stage:'complete',message:'Ran 2 Python script(s) and saved Orders extract.csv. Committed 1284 row(s) to analytics.public.orders_clean. Refreshed 1 materialized view(s).'},artifacts:[{file_path:'Python/Orders extract/Downloads/#51_15-09-2026/Orders extract.csv',filename:'Orders extract.csv',row_count:1284,file_size:88211,status:'saved'}],
            timings:[{phase:'python_scripts',duration_ms:41200,status:'succeeded'},{phase:'file_normalization',duration_ms:300,status:'succeeded'},{phase:'sql_insertion',duration_ms:5200,status:'succeeded'},{phase:'view_refresh',duration_ms:15200,status:'succeeded'},{phase:'total',duration_ms:67000,status:'succeeded'}],
            sql_outcome:{committed:true,at:'2026-09-15T02:30:55Z',rows_written:1284,files_loaded:1,target:'analytics.public.orders_clean'},view_refresh:{mode:'manual',deferred_to_pipeline:false,total:1,completed:1,sql_committed:true,retry:null,views:[{sequence_no:1,database:'analytics',schema:'bi',name:'orders_by_region_mv',status:'succeeded',duration_ms:15200,error:null,finished_at:'2026-09-15T02:31:10Z'}]},
            events:[],files:[],downloads:null,email:null},
        {id:50,flow_id:22,flow_name:'Supplier lead times',status:'succeeded',trigger_type:'manual',requested_by:'Planner',worker_id:'headless-2',created_at:'2026-09-15T02:30:00Z',started_at:'2026-09-15T02:30:02Z',finished_at:'2026-09-15T02:30:40Z',error:null,job:pythonJob(data.flows[1]),
            progress:{stage:'complete',message:'Ran 1 Python script(s) and saved Supplier lead times_2026-09-15.xlsx.'},artifacts:[{file_path:'Python/Supplier lead times/Downloads/Supplier lead times_2026-09-15.xlsx',filename:'Supplier lead times_2026-09-15.xlsx',file_size:40120,status:'saved'}],
            timings:[{phase:'python_scripts',duration_ms:36000,status:'succeeded'},{phase:'total',duration_ms:38000,status:'succeeded'}],sql_outcome:null,view_refresh:null,events:[],files:[],downloads:null,
            email:{status:'submitted',detail:null,dispatch_id:311,recipients:['planner@example.test'],subject:null,files:['Supplier lead times_2026-09-15.xlsx'],label:'submitted by Outlook'}},
    ];
    window._flowsState={catalog:data.catalog,flows:data.flows,people:data.people,runs:data.runs,scans:[],workers:[{id:'headless-2',status:'online'}],estimates:{},sqlCatalog:data.sqlCatalog,groups:[],view:'source-picker'};
    window.previewPayload=null;window.previewCalls=[];
}
window.previewData=data;
window._flowWatchExecutionPane=()=>{};window._flowScheduleCatalogMonitor=()=>{};window._flowStopActivityMonitor=()=>{};window._flowRefreshActivity=()=>Promise.resolve();
window.toast=status;
window.navigate=async page=>{if(page==='flows')show('list');};
const record=(method,path,body)=>window.previewCalls.push({method,path,body:body?structuredClone(body):undefined});
const flowId=path=>Number(path.match(/\/api\/flows\/(\d+)/)?.[1]);
const outcome=()=>document.getElementById('preview-outcome').value;
function validationError(){
    const message='Python scripts must be .py files.';
    return Object.assign(Error(message),{validation:[{loc:['body','python_scripts'],msg:message}]});
}
function orderViews(views){
    const key=v=>`${v.schema}.${v.name}`;const selected=new Map(views.map(v=>[key(v),v]));const out=[];const seen=new Set();
    const visit=v=>{if(seen.has(key(v)))return;seen.add(key(v));for(const up of data.upstream[key(v)]||[])if(selected.has(up))visit(selected.get(up));out.push(v);};
    views.forEach(visit);return out;
}
function savedFlow(body,id,existing){
    const name=body.name||existing?.name||'New flow';
    const merged={...(existing||pythonFlow(id,name)),...body,id,name,source_adapter:'python_script',flow_folder:`Python/${name}`,folder_relative:`Python/${name}`,owner_name:data.people.find(p=>p.id===body.owner_person_id)?.name||null,
        post_sql_refresh:body.post_sql_refresh||{mode:'off',views:[]},email_delivery:body.email_delivery||{...emailOff},sql_target_link_status:body.sql_handoff_enabled?'confirmed':null,sql_target_source_id:body.sql_handoff_enabled?31:null};
    if(!existing){merged.last_run_at=null;merged.last_status=null;}
    return merged;
}
window.api=async path=>{
    record('GET',path);
    if(path==='/api/flows/sql/catalog')return structuredClone(data.sqlCatalog);
    if(path==='/api/flows')return structuredClone(data.flows);
    if(path==='/api/flows/runs')return structuredClone(data.runs);
    if(path==='/api/people')return structuredClone(data.people);
    if(path.startsWith('/api/flows/view-refresh/catalog')){const q=decodeURIComponent(path.split('q=')[1]||'').toLowerCase();return {server:'warehouse.example.test',metadata_at:'2026-09-15T22:00:00Z',views:data.views.filter(v=>!q||`${v.database}.${v.schema}.${v.name}`.toLowerCase().includes(q)).map(v=>({...v,key:`${v.database}|${v.schema}|${v.name}`}))};}
    throw Error('Preview only. No live API requests.');
};
window.apiPostJson=async(path,body)=>{
    record('POST',path,body);
    if(path==='/api/flows/view-refresh/discover')return {mode:'automatic',target:{},discovered_at:now(),metadata_at:'2026-09-15T22:00:00Z',stale:false,warnings:[],blockers:[],server:'warehouse.example.test',status:'ok',views:data.views.slice(0,2).map(v=>({...v,key:`analytics|bi|${v.name}`}))};
    if(path==='/api/flows/view-refresh/verify'){
        const views=(body.views||[]).map(v=>({...v,key:`${v.database}|${v.schema}|${v.name}`}));
        const known=v=>data.views.some(k=>k.schema===v.schema&&k.name===v.name);
        const blockers=views.filter(v=>!known(v)).map(v=>`${v.database}.${v.schema}.${v.name} does not exist on the configured SQL server.`);
        return {mode:'manual',status:blockers.length?'blocked':'ok',views:orderViews(views).map(v=>({...v,verified:known(v),catalog:known(v)})),blockers,warnings:[],server:'warehouse.example.test',discovered_at:now()};
    }
    if(path==='/api/flows'){
        if(outcome()==='invalid')throw validationError();
        const id=Math.max(0,...data.flows.map(f=>f.id))+1;const created=savedFlow(body,id,null);data.flows.push(created);window.previewPayload=body;
        status(`Created "${created.name}" in preview · ${body.python_scripts.length} script(s) → ${body.sql_handoff_enabled?`SQL ${body.sql_database}.${body.sql_schema}.${body.sql_table}`:`${body.file_format.toUpperCase()} file ${body.filename_template}`}.`);
        return {...created,standalone:{state:'current'}};
    }
    throw Error('Preview only.');
};
window.apiPut=async(path,body)=>{
    record('PUT',path,body);
    if(outcome()==='invalid')throw validationError();
    const id=flowId(path);const index=data.flows.findIndex(f=>f.id===id);if(index<0)throw Error('Flow not found.');
    const target=savedFlow(body,id,data.flows[index]);data.flows[index]=target;window._flowsState.flows=data.flows;window.previewPayload=body;
    status(`Saved "${target.name}" in preview · ${body.python_scripts.length} script(s) → ${body.sql_handoff_enabled?`SQL ${body.sql_database}.${body.sql_schema}.${body.sql_table}`:`${body.file_format.toUpperCase()} file ${body.filename_template}`}${body.post_sql_refresh?.mode==='manual'?` · refresh ${body.post_sql_refresh.views.length} view(s)`:''}.`);
    return {...target,standalone:{state:'current'}};
};
window.apiPatch=async(path,body)=>{record('PATCH',path,body);const target=data.flows.find(f=>f.id===flowId(path));if(target)Object.assign(target,body);return {...target,...body};};
window.apiPostForm=async(path,body)=>{
    record('POST',path);
    if(path==='/api/flows/transform-script'){const file=body.get('file');const filename=(file?.name||'script.py');return {script_path:`C:\\Metronome\\Flows\\.metronome\\uploads\\3f2c9a\\${filename}`,filename,file_size:file?.size||0};}
    throw Error('Preview only.');
};
window.apiPost=async path=>{
    record('POST',path);
    if(path==='/api/flows/sql/catalog/refresh')return {accepted:true};
    if(path.endsWith('/open-folder')){status('Output folder would open here.');return {opened:true};}
    if(path.endsWith('/run')){const flow=data.flows.find(f=>f.id===flowId(path));const id=Math.max(0,...data.runs.map(r=>r.id))+1;
        data.runs.unshift({id,flow_id:flow.id,flow_name:flow.name,status:'queued',trigger_type:'manual',requested_by:'Analyst',worker_id:null,created_at:now(),started_at:null,finished_at:null,error:null,job:pythonJob(flow),progress:{stage:'queued',message:'Waiting for a worker with the python_script adapter.'},artifacts:[],timings:[],sql_outcome:null,view_refresh:null,events:[],files:[],downloads:null,email:null});
        window._flowsState.runs=data.runs;return {id};}
    throw Error('Preview only.');
};
window.apiDelete=async path=>{record('DELETE',path);return {};};
function show(screen,payload){
    document.querySelectorAll('.preview-nav button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.screen===screen)));
    if(screen==='picker')window._flowShowView('source-picker');
    else if(screen==='new')window._flowShowView('builder',{_source_type:'python'});
    else if(screen==='builder')window._flowShowView('builder',payload||data.flows.find(f=>f.id===21));
    else if(screen==='list')window._flowShowView('list');
    else if(screen==='runs')window._flowShowView('runs');
}
window.previewShow=show;
document.querySelectorAll('.preview-nav button').forEach(b=>b.onclick=()=>show(b.dataset.screen));
document.getElementById('preview-reset').onclick=()=>{reset();status('Preview reset.');show('picker');};
document.getElementById('preview-outcome').onchange=()=>status(outcome()==='invalid'?'The next save fails validation: "Python scripts must be .py files."':'The next save succeeds.');
reset();
show('picker');
