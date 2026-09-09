// Fictional, in-memory preview of reusable recordings and post-SQL view refresh.
// Production app.js, recording editor and run-log page render every screen;
// only the API layer is replaced. No worker, portal, PostgreSQL or file access.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js?v=1', '/static/app.js?v=71', '/static/flow_recording_editor.js?v=13', '/static/flow_recordings.js?v=4']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const status=message=>{document.getElementById('preview-status').textContent=message;};
const now=()=>new Date().toISOString();
const button=name=>[{method:'get_by_role',args:['button'],kwargs:{name}}];
const step=(id,action,extra={})=>({id,action,page:'page',locator:[],args:[],kwargs:{},...extra});
function recording(name,extra=[]){
    return {version:2,timezone:'Asia/Dubai',adapter:'gscm_portal',parameters:{period_start:{mode:'calculated',expression:'month_start',format:'%Y-%m-%d',step_id:`${name}-start`}},steps:[
        step(`${name}-open`,'goto',{args:['https://gscm.example.test/home']}),
        step(`${name}-setting`,'click',{locator:button('Setting')}),
        step(`${name}-bookmark`,'click',{locator:[{method:'get_by_text',args:['Regional sales'],kwargs:{exact:true}}],bookmark_target:{kind:'gscm_favorite',bookmark_name:'Regional sales'}}),
        step(`${name}-start`,'fill',{locator:[{method:'get_by_label',args:['Start'],kwargs:{}}],args:['2026-09-01']}),
        step(`${name}-wait`,'wait',{seconds:20}),
        ...extra,
        step(`${name}-download`,'download',{steps:[step(`${name}-export`,'click',{locator:button('Download Excel')})],output:{format:'xlsx',min_rows:4}})
    ]};
}
const data={
    catalog:{sites:[{id:1,name:'GSCM',adapter:'gscm_portal',enabled:true},{id:2,name:'ASAP',adapter:'asap_portal',enabled:true}],reports:[],asap_download_types:[{key:'excel',file_format:'xlsx',preferred_suffix:'.xlsx',label:'Excel workbook'}]},
    sqlCatalog:{configured:true,targets:[{database:'analytics',schema:'public',table:'regional_orders'},{database:'analytics',schema:'public',table:'inventory_aging'},{database:'analytics',schema:'staging',table:'returns_raw'}],scan:{last_scan_at:now(),duration_ms:4200},missing:[]},
    flows:[],revisions:{},sessions:{},runs:[],
    views:[{database:'analytics',schema:'bi',name:'regional_orders_mv',source_name:'bi.regional_orders_mv'},{database:'analytics',schema:'bi',name:'orders_by_region_mv',source_name:'bi.orders_by_region_mv'},{database:'analytics',schema:'bi',name:'executive_summary_mv',source_name:'bi.executive_summary_mv'},{database:'analytics',schema:'bi',name:'inventory_aging_mv',source_name:'bi.inventory_aging_mv'}],
    upstream:{'bi.orders_by_region_mv':['bi.regional_orders_mv'],'bi.executive_summary_mv':['bi.orders_by_region_mv']}
};
function flow(id,name,site_id,extra={}){
    return {id,name,source_type:'portal',site_id,site_name:site_id===1?'GSCM':'ASAP',source_adapter:site_id===1?'gscm_portal':'asap_portal',report_id:id,execution_method:'recorded',flow_folder:`${site_id===1?'GSCM':'ASAP'}/${name} (id ${id})`,folder_relative:`${site_id===1?'GSCM':'ASAP'}/${name} (id ${id})`,target_folder:null,output_mode:'direct_replace',filename_template:'orders_{export}.xlsx',period_strategy:'none',file_format:'xlsx',schedule_type:'manual',browser_mode:'headed',selections:{},enabled:false,
        sql_handoff_enabled:true,sql_mode:'append',sql_database:'analytics',sql_schema:'public',sql_table:'regional_orders',sql_target_link_status:'confirmed',post_sql_refresh:{mode:'off',views:[]},recording_revision_id:null,last_run_at:null,last_status:null,...extra};
}
function reset(){
    data.flows=[
        flow(7,'Regional orders',1,{recording_revision_id:3,post_sql_refresh:{mode:'automatic',views:[]}}),
        flow(8,'Inventory aging',1,{sql_table:'inventory_aging',post_sql_refresh:{mode:'manual',views:[{database:'analytics',schema:'bi',name:'inventory_aging_mv'}]}}),
        flow(9,'Sell-out weekly',2,{recording_revision_id:6,sql_handoff_enabled:false}),
        flow(10,'Warehouse snapshot',1,{execution_method:'catalog'}),
        flow(11,'Returns report',1,{sql_table:'returns_raw',sql_schema:'staging'}),
    ];
    data.revisions={7:[{id:4,status:'draft',created_at:'2026-09-06T09:30:00Z',definition:recording('regional',[step('regional-filter','click',{locator:button('Apply filter')})])},{id:3,status:'validated',created_at:'2026-09-04T08:00:00Z',validated_at:'2026-09-04T08:20:00Z',definition:recording('regional')}],
        8:[{id:5,status:'draft',created_at:'2026-09-05T13:10:00Z',definition:recording('aging')}],
        9:[{id:6,status:'validated',created_at:'2026-09-03T10:00:00Z',validated_at:'2026-09-03T10:30:00Z',definition:{...recording('sellout'),adapter:'asap_portal'}}],
        11:[]};
    data.sessions={7:[],8:[],9:[],11:[]};
    const views=[{sequence_no:1,database:'analytics',schema:'bi',name:'regional_orders_mv',status:'succeeded',duration_ms:3400,error:null,started_at:'2026-09-07T05:10:02Z',finished_at:'2026-09-07T05:10:05Z'},
        {sequence_no:2,database:'analytics',schema:'bi',name:'orders_by_region_mv',status:'failed',duration_ms:600100,error:'PostgreSQL SQLSTATE 55P03: canceling statement due to lock timeout',started_at:'2026-09-07T05:10:05Z',finished_at:'2026-09-07T05:20:05Z'},
        {sequence_no:3,database:'analytics',schema:'bi',name:'executive_summary_mv',status:'skipped',duration_ms:null,error:'Not attempted because an upstream view failed.',started_at:null,finished_at:null}];
    const plan={mode:'automatic',discovered_at:'2026-09-07T05:00:00Z',metadata_at:'2026-09-06T22:00:00Z',server:'warehouse.example.test',views:views.map(v=>({database:v.database,schema:v.schema,name:v.name}))};
    const job=(id,extra={})=>({flow:{id:7,name:'Regional orders',execution_method:'recorded',source_type:'portal'},execution:{browser_mode:'headed'},downloads:{periods:[null],output_mode:'direct_replace'},report:{export_views:[]},transformation:{enabled:false},sql_handoff:{enabled:true,mode:'append',database:'analytics',schema:'public',table:'regional_orders'},post_sql_refresh:plan,...extra});
    data.runs=[
        {id:41,flow_id:7,flow_name:'Regional orders',status:'failed',trigger_type:'manual',requested_by:'Analyst',worker_id:'headed-1',created_at:'2026-09-07T05:08:00Z',started_at:'2026-09-07T05:08:20Z',finished_at:'2026-09-07T05:20:06Z',error:'Materialized view refresh failed for analytics.bi.orders_by_region_mv: PostgreSQL SQLSTATE 55P03: canceling statement due to lock timeout SQL insertion had already committed; use Retry view refresh to finish the remaining views.',
            job:job(41),progress:{stage:'failed',message:'Materialized view refresh failed'},artifacts:[{file_path:'GSCM/Regional orders (id 7)/Downloads/orders_1.xlsx',filename:'orders_1.xlsx',row_count:1284,file_size:88211,status:'saved'}],
            timings:[{phase:'sql_insertion',duration_ms:5200,status:'succeeded'},{phase:'view_refresh',duration_ms:603500,status:'failed'},{phase:'total',duration_ms:706000,status:'failed'}],
            sql_outcome:{committed:true,at:'2026-09-07T05:10:01Z',rows_written:1284,files_loaded:1,target:'analytics.public.regional_orders'},
            view_refresh:{mode:'automatic',deferred_to_pipeline:false,discovered_at:plan.discovered_at,metadata_at:plan.metadata_at,total:3,completed:1,views,sql_committed:true,retry:{status:'eligible',reason_code:'views_pending',message:'2 of 3 materialized view(s) still need refreshing; SQL insertion stays committed.'}},
            events:[{id:1,status:'running',stage:'sql_insertion',message:'Loading downloaded files into SQL.',details:{},created_at:'2026-09-07T05:09:55Z'},{id:2,status:'running',stage:'sql_insertion_complete',message:'Inserted 1284 row(s) from 1 file(s).',details:{rows_written:1284},created_at:'2026-09-07T05:10:01Z'},{id:3,status:'running',stage:'view_refresh',message:'SQL insertion committed → Refreshing materialized views (1 of 3): analytics.bi.regional_orders_mv',details:{},created_at:'2026-09-07T05:10:02Z'},{id:4,status:'running',stage:'view_refresh',message:'SQL insertion committed → Refreshing materialized views (2 of 3): analytics.bi.orders_by_region_mv',details:{},created_at:'2026-09-07T05:10:05Z'},{id:5,status:'running',stage:'view_refresh_failed',message:'Materialized view refresh failed for analytics.bi.orders_by_region_mv: lock timeout. SQL insertion had already committed; use Retry view refresh to finish the remaining views.',details:{},created_at:'2026-09-07T05:20:05Z'},{id:6,status:'failed',stage:'failed',message:'Materialized view refresh failed',details:{},error:'lock timeout',created_at:'2026-09-07T05:20:06Z'}],files:[],downloads:null},
        {id:40,flow_id:8,flow_name:'Inventory aging',status:'succeeded',trigger_type:'scheduled',requested_by:null,worker_id:'headed-1',created_at:'2026-09-07T04:00:00Z',started_at:'2026-09-07T04:00:10Z',finished_at:'2026-09-07T04:06:40Z',error:null,job:job(40,{flow:{id:8,name:'Inventory aging',execution_method:'recorded',source_type:'portal'},post_sql_refresh:{mode:'manual',discovered_at:'2026-09-07T04:00:00Z',views:[{database:'analytics',schema:'bi',name:'inventory_aging_mv'}]}}),progress:{stage:'complete',message:'Saved the full 1-export bundle and committed 412 row(s) to analytics.public.inventory_aging. Refreshed 1 materialized view(s).'},artifacts:[{file_path:'x',filename:'aging_1.xlsx',status:'saved'}],timings:[{phase:'sql_insertion',duration_ms:2100,status:'succeeded'},{phase:'view_refresh',duration_ms:15200,status:'succeeded'},{phase:'total',duration_ms:390000,status:'succeeded'}],sql_outcome:{committed:true,rows_written:412,files_loaded:1},view_refresh:{mode:'manual',deferred_to_pipeline:false,total:1,completed:1,views:[{sequence_no:1,database:'analytics',schema:'bi',name:'inventory_aging_mv',status:'succeeded',duration_ms:15200,error:null,finished_at:'2026-09-07T04:06:30Z'}],sql_committed:true,retry:null},events:[],files:[],downloads:null},
        {id:39,flow_id:9,flow_name:'Sell-out weekly',status:'succeeded',trigger_type:'manual',requested_by:'Analyst',worker_id:'headed-1',created_at:'2026-09-06T04:00:00Z',started_at:'2026-09-06T04:00:10Z',finished_at:'2026-09-06T04:03:00Z',error:null,job:{flow:{id:9,name:'Sell-out weekly',execution_method:'recorded',source_type:'portal'},execution:{browser_mode:'headed'},downloads:{periods:[null]},report:{export_views:[]},transformation:{enabled:false},sql_handoff:{enabled:false},post_sql_refresh:{mode:'off',views:[]}},progress:{stage:'complete',message:'Saved 1 XLSX export(s).'},artifacts:[{file_path:'x',filename:'sellout.xlsx',status:'saved'}],timings:[{phase:'total',duration_ms:170000,status:'succeeded'}],sql_outcome:null,view_refresh:null,events:[],files:[],downloads:null},
    ];
    window._flowsState={catalog:data.catalog,flows:data.flows,people:[],runs:data.runs,scans:[],workers:[],estimates:{},sqlCatalog:data.sqlCatalog,view:'builder'};
    window.previewPayload=null;window.previewCalls=[];
}
window.previewData=data;
window._flowWatchExecutionPane=()=>{};window._flowScheduleCatalogMonitor=()=>{};window._flowStopActivityMonitor=()=>{};window._flowRefreshActivity=()=>Promise.resolve();
window.toast=status;window.navigate=async()=>{};
const record=(method,path,body)=>window.previewCalls.push({method,path,body:body?structuredClone(body):undefined});
const flowId=path=>Number(path.match(/\/api\/flows\/(\d+)/)?.[1]);
function discovery(){
    const scenario=document.getElementById('preview-discovery').value;
    const base={mode:'automatic',target:{},discovered_at:now(),metadata_at:'2026-09-06T22:00:00Z',stale:false,warnings:[],blockers:[],server:'warehouse.example.test'};
    const three=[0,1,2].map(i=>({...data.views[i],key:`analytics|bi|${data.views[i].name}`}));
    switch(scenario){
        case 'empty': return {...base,status:'ok',views:[]};
        case 'missing': return {...base,status:'missing',metadata_at:null,blockers:['analytics.public.regional_orders is not in the PostgreSQL dependency metadata yet. Refresh the metadata (Scanner → PostgreSQL lineage) or choose Manual.']};
        case 'incomplete': return {...base,status:'incomplete',blockers:['Downstream relation bi.orders_by_region_mv has no verified PostgreSQL identity; refresh the metadata or choose Manual.']};
        case 'stale': return {...base,status:'ok',views:three,stale:true,metadata_at:'2026-09-02T22:00:00Z',warnings:['The dependency metadata is older than 48 hours; refresh it to be sure the list is current.']};
        case 'cyclic': return {...base,status:'cyclic',blockers:['Materialized-view dependency cycle: analytics.bi.orders_by_region_mv → analytics.bi.executive_summary_mv → analytics.bi.orders_by_region_mv. Fix the lineage or choose Manual.']};
        default: return {...base,status:'ok',views:three};
    }
}
function orderViews(views){
    const key=v=>`${v.schema}.${v.name}`;const selected=new Map(views.map(v=>[key(v),v]));const out=[];const seen=new Set();
    const visit=v=>{if(seen.has(key(v)))return;seen.add(key(v));for(const up of data.upstream[key(v)]||[])if(selected.has(up))visit(selected.get(up));out.push(v);};
    views.forEach(visit);return out;
}
window.api=async path=>{
    record('GET',path);
    if(path==='/api/flows/sql/catalog')return structuredClone(data.sqlCatalog);
    if(path.startsWith('/api/flows/view-refresh/catalog')){const q=decodeURIComponent(path.split('q=')[1]||'').toLowerCase();return {server:'warehouse.example.test',metadata_at:'2026-09-06T22:00:00Z',views:data.views.filter(v=>!q||`${v.database}.${v.schema}.${v.name}`.toLowerCase().includes(q)).map(v=>({...v,key:`${v.database}|${v.schema}|${v.name}`}))};}
    const id=flowId(path);const target=data.flows.find(f=>f.id===id);if(!target)throw Error('Flow not found.');
    if(/\/recordings\/templates\/(\d+)\/revisions\/(\d+)/.test(path)){
        const [,sourceId,revisionId]=path.match(/templates\/(\d+)\/revisions\/(\d+)/).map(Number);
        const source=data.flows.find(f=>f.id===sourceId);const module=moduleFor(target,path);
        if(source.source_adapter!==module)throw Error(`That recording belongs to ${source.source_adapter==='gscm_portal'?'GSCM':'ASAP'} and cannot be used for a ${module==='gscm_portal'?'GSCM':'ASAP'} Flow.`);
        const revision=(data.revisions[sourceId]||[]).find(r=>r.id===revisionId);if(!revision)throw Error('That recording version no longer exists.');
        return {source_flow_id:sourceId,source_name:source.name,website:source.site_name,revision_id:revision.id,status:revision.id===source.recording_revision_id?'active':revision.status,created_at:revision.created_at,definition:structuredClone(revision.definition),step_count:RecordedFlowModel.all(revision.definition.steps).length,provenance:null};
    }
    if(path.includes('/recordings/templates')){
        const module=moduleFor(target,path);
        const templates=data.flows.filter(f=>f.id!==id&&f.source_adapter===module&&(data.revisions[f.id]||[]).length).sort((a,b)=>a.name.localeCompare(b.name)).map(f=>{const revisions=data.revisions[f.id];const active=revisions.find(r=>r.id===f.recording_revision_id);const def=active||revisions[0];return {flow_id:f.id,name:f.name,website:f.site_name,module,step_count:RecordedFlowModel.all(def.definition.steps).length,recording_status:active?'active':def.status,default_revision_id:def.id,revisions:revisions.map(r=>({id:r.id,status:r.id===f.recording_revision_id?'active':r.status,created_at:r.created_at,validated_at:r.validated_at||null,step_count:RecordedFlowModel.all(r.definition.steps).length,from_template:Boolean(r.template_source)}))};});
        return {module,module_label:module==='gscm_portal'?'GSCM':'ASAP',templates};
    }
    if(path.endsWith('/recordings'))return {flow:structuredClone(target),revisions:structuredClone(data.revisions[id]||[]),sessions:structuredClone(data.sessions[id]||[]),recording_wait_seconds:10};
    throw Error('Preview only. No live API requests.');
};
function moduleFor(target,path){const pending=Number((path.match(/site_id=(\d+)/)||[])[1]);const site=pending?data.catalog.sites.find(s=>s.id===pending):null;return site?site.adapter:target.source_adapter;}
window.apiPostJson=async(path,body)=>{
    record('POST',path,body);
    if(path==='/api/flows/view-refresh/discover')return discovery();
    if(path==='/api/flows/view-refresh/verify'){
        const views=(body.views||[]).map(v=>({...v,key:`${v.database}|${v.schema}|${v.name}`}));
        const known=v=>data.views.some(k=>k.schema===v.schema&&k.name===v.name);
        const blockers=views.filter(v=>!known(v)).map(v=>`${v.database}.${v.schema}.${v.name} does not exist on the configured SQL server.`);
        return {mode:'manual',status:blockers.length?'blocked':'ok',views:orderViews(views).map(v=>({...v,verified:known(v),catalog:known(v)})),blockers,warnings:[],server:'warehouse.example.test',discovered_at:now()};
    }
    if(path==='/api/flows'){const id=12;const created=flow(id,body.name||'New flow',body.site_id||1,{...body,id,post_sql_refresh:body.post_sql_refresh||{mode:'off',views:[]}});data.flows.push(created);data.revisions[id]=[];data.sessions[id]=[];window.previewPayload=body;status(`Created "${created.name}" in preview (paused).`);return {...created,standalone:{state:'current'}};}
    const id=flowId(path);
    if(path.endsWith('/recordings/revisions/copy')){
        const target=data.flows.find(f=>f.id===id);const source=data.flows.find(f=>f.id===body.source_flow_id);const module=moduleFor(target,body.site_id?`?site_id=${body.site_id}`:'');
        if(!source||!(data.revisions[source.id]||[]).length)throw Error('That Flow has no saved recording yet.');
        if(source.source_adapter!==module)throw Error(`That recording belongs to ${source.source_adapter==='gscm_portal'?'GSCM':'ASAP'} and cannot be used for a ${module==='gscm_portal'?'GSCM':'ASAP'} Flow.`);
        const revisions=data.revisions[source.id];const revision=body.source_revision_id?revisions.find(r=>r.id===body.source_revision_id):(revisions.find(r=>r.id===source.recording_revision_id)||revisions[0]);
        const provenance={source_flow_id:source.id,source_flow_name:source.name,source_revision_id:revision.id,source_status:revision.id===source.recording_revision_id?'active':revision.status,copied_at:now()};
        const nextId=Math.max(0,...Object.values(data.revisions).flat().map(r=>r.id))+1;
        (data.revisions[id]||(data.revisions[id]=[])).unshift({id:nextId,status:'draft',created_at:now(),definition:structuredClone(revision.definition),template_source:provenance});
        return {revision_id:nextId,definition:structuredClone(revision.definition),provenance};
    }
    if(path.endsWith('/validate')){const revision=data.revisions[id][0];revision.status='validated';data.sessions[id].unshift({scan_id:Date.now(),revision_id:revision.id,operation:'validate',status:'succeeded',progress_json:JSON.stringify({step_outcomes:Object.fromEntries(RecordedFlowModel.all(revision.definition.steps).map(s=>[s.id,{outcome:'completed'}]))})});return {revision_id:revision.id};}
    if(path.endsWith('/recordings/revisions')){const nextId=Math.max(0,...Object.values(data.revisions).flat().map(r=>r.id))+1;data.revisions[id].unshift({id:nextId,status:'draft',created_at:now(),definition:structuredClone(body.definition)});return {revision_id:nextId};}
    throw Error('Preview only.');
};
window.apiPut=async(path,body)=>{record('PUT',path,body);const id=flowId(path);const target=data.flows.find(f=>f.id===id);if(window.previewFail)throw Error('Save failed. Your changes are kept.');Object.assign(target,body);window.previewPayload=body;status(`Saved "${target.name}" in preview · refresh views: ${body.post_sql_refresh?.mode||'off'}${body.post_sql_refresh?.mode==='manual'?` (${body.post_sql_refresh.views.length} chosen)`:''}.`);return {...target,standalone:{state:'current'}};};
window.apiPatch=async(path,body)=>{record('PATCH',path,body);return body;};
window.apiPost=async path=>{
    record('POST',path);
    if(path==='/api/scanner/jobs/postgres-lineage')return {accepted:true};
    if(path.includes('/retry-views')){const source=data.runs.find(r=>r.id===Number(path.match(/runs\/(\d+)/)[1]));queueRetry(source);return {id:44,remaining_views:2};}
    if(path.endsWith('/open-folder')){status('Output folder would open here.');return {opened:true};}
    const id=flowId(path);
    if(path.endsWith('/start')){data.sessions[id].unshift({scan_id:Date.now(),revision_id:null,operation:'record',status:'running',progress_json:JSON.stringify({stage:'recording',message:'Recording fictional actions…'})});return {};}
    if(path.endsWith('/finish')){const nextId=Math.max(0,...Object.values(data.revisions).flat().map(r=>r.id))+1;data.revisions[id].unshift({id:nextId,status:'draft',created_at:now(),definition:recording('fresh')});data.sessions[id][0].status='succeeded';return {};}
    if(path.endsWith('/cancel')){data.sessions[id][0].status='cancelled';return {};}
    throw Error('Preview only.');
};
function queueRetry(source){
    const fail=document.getElementById('preview-retry').value==='fail';
    const views=structuredClone(source.view_refresh.views);
    views[1]=fail?{...views[1],status:'failed',error:'PostgreSQL SQLSTATE 55P03: canceling statement due to lock timeout',finished_at:now()}:{...views[1],status:'succeeded',error:null,duration_ms:41000,finished_at:now()};
    views[2]=fail?{...views[2],status:'skipped'}:{...views[2],status:'succeeded',error:null,duration_ms:9000,finished_at:now()};
    const retry={...structuredClone(source),id:44,status:fail?'failed':'succeeded',trigger_type:'view_retry',requested_by:'Analyst',created_at:now(),started_at:now(),finished_at:now(),
        error:fail?'Materialized view refresh failed for analytics.bi.orders_by_region_mv: lock timeout. SQL insertion had already committed; use Retry view refresh to finish the remaining views.':null,
        job:{...source.job,job_type:'view_retry',view_retry:{source_run_id:source.id,completed:['analytics|bi|regional_orders_mv']}},
        progress:{stage:fail?'failed':'complete',message:fail?'Materialized view refresh failed':'Refreshed 3 materialized view(s); SQL insertion from run #41 was already committed.'},
        timings:[{phase:'view_refresh',duration_ms:fail?600100:50000,status:fail?'failed':'succeeded'},{phase:'total',duration_ms:fail?600200:50100,status:fail?'failed':'succeeded'}],
        sql_outcome:{...source.sql_outcome,inherited_from_run_id:source.id},
        view_refresh:{...source.view_refresh,source_run_id:source.id,completed:views.filter(v=>v.status==='succeeded').length,views,retry:fail?{status:'eligible',reason_code:'views_pending',message:'2 of 3 materialized view(s) still need refreshing; SQL insertion stays committed.'}:null},
        events:[{id:1,status:'running',stage:'view_retry',message:'Retrying the materialized-view refresh from run #41: 1 view(s) already refreshed are kept. No download, transformation or SQL insertion runs.',details:{},created_at:now()}]};
    source.view_refresh.retry={status:'blocked',reason_code:'superseded',message:'Retried by run #44.'};
    data.runs=[retry,...data.runs.filter(r=>r.id!==44)];window._flowsState.runs=data.runs;
    status(fail?'View refresh queued in preview: run #44 failed again on the same view.':'View refresh queued in preview: run #44 refreshed the remaining 2 views without repeating SQL insertion.');
    show('runs');
}
function show(screen,payload){
    document.querySelectorAll('.preview-nav button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.screen===screen)));
    const workspace=document.getElementById('flow-workspace'),frame=document.getElementById('preview-log');
    document.querySelector('.flow-recording-page')?.remove();
    frame.hidden=screen!=='log';workspace.hidden=screen==='log';
    if(screen==='builder'){window._flowsState.view='builder';window._flowShowView('builder',payload||data.flows.find(f=>f.id===7));}
    else if(screen==='new'){window._flowsState.view='builder';window._flowShowView('builder',{site_id:1,execution_method:'recorded'});}
    else if(screen==='recording'){workspace.innerHTML='';window.FlowRecordings.open(payload||11,{name:data.flows.find(f=>f.id===(payload||11)).name,site_id:1});}
    else if(screen==='runs'){window._flowsState.view='runs';window._flowShowView('runs');}
    else if(screen==='log'){const runs=Object.fromEntries(data.runs.map(r=>[r.id,r]));frame.contentWindow.postMessage({type:'show-run',runs,runId:payload||(runs[44]?44:41)},'*');}
}
window.previewShow=show;
window.addEventListener('message',event=>{if(event.data?.type==='retry-views'){const source=data.runs.find(r=>r.id===event.data.runId);queueRetry(source);show('log',44);}});
document.querySelectorAll('.preview-nav button').forEach(b=>b.onclick=()=>show(b.dataset.screen));
document.getElementById('preview-reset').onclick=()=>{reset();status('Preview reset.');show('builder');};
document.getElementById('preview-discovery').onchange=()=>status('Discovery scenario changed; use Preview discovered views in Edit Flow.');
reset();
show('builder');
