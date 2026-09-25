// Fictional, in-memory preview of a PROPOSED builder choice: whether a Python
// Flow keeps a managed Metronome folder. Production app.js renders the Python
// builder and the Flows list; this file replaces the API layer and adds the
// proposed "Flow folder" control, which app.js does not have yet. No worker,
// Python, file or folder access happens here.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js?v=2', '/static/app.js?v=80', '/static/flow_recording_editor.js?v=13', '/static/flow_recordings.js?v=4']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const ROOT='C:\\Metronome\\Flows';
const status=message=>{document.getElementById('preview-status').textContent=message;};
const now=()=>new Date().toISOString();
const emailOff={enabled:false,recipients:[],subject:null};
const data={
    catalog:{sites:[],reports:[],asap_download_types:[]},
    sqlCatalog:{configured:true,targets:[{database:'analytics',schema:'public',table:'orders_clean'}],scan:{last_scan_at:now(),duration_ms:1200},missing:[]},
    people:[{id:1,name:'Ops analyst',role:'Analyst',email:'ops@example.test',sql_username:null}],
    flows:[],runs:[],
};
function pythonFlow(id,name,extra={}){
    return {id,name,source_type:'python',source_adapter:'python_script',site_id:null,report_id:null,site_name:'Python',
        python_scripts:['C:\\Reports\\excel_jobs\\refresh_prices.py'],python_script_arguments:[''],python_script_values:[[]],
        python_run_mode:'run',python_timeout_minutes:60,python_interpreter:null,execution_method:'catalog',classification:'production',
        managed_folder:true,flow_folder:`${ROOT}\\Python\\${name}`,folder_relative:`Python/${name}`,target_folder:`${ROOT}\\Python\\${name}\\Downloads`,
        output_mode:'run_folders',filename_template:'run-only.csv',file_format:'csv',period_strategy:'none',download_mode:'single',browser_mode:'headless',selections:{},export_views:[],
        schedule_type:'daily',schedule_time:'06:45',schedule_days:[],schedule_day:1,enabled:true,owner_person_id:1,owner_name:'Ops analyst',transform_enabled:false,transform_script_path:null,
        sql_handoff_enabled:false,sql_mode:null,sql_uppercase:false,sql_database:null,sql_schema:null,sql_table:null,sql_target_link_status:null,sql_target_source_id:null,
        post_sql_refresh:{mode:'off',views:[]},email_delivery:{...emailOff},recording_revision_id:null,last_run_at:'2026-09-24T02:45:00Z',last_status:'succeeded',...extra};
}
function reset(){
    data.flows=[
        pythonFlow(31,'Excel price refresh'),
        pythonFlow(32,'Orders extract',{python_run_mode:'outputs',python_scripts:['D:\\Analytics\\orders\\fetch_orders.py'],filename_template:'{flow}.csv',last_status:'failed'}),
    ];
    window._flowsState={catalog:data.catalog,flows:data.flows,people:data.people,runs:data.runs,scans:[],workers:[{id:'headless-2',status:'online'}],estimates:{},sqlCatalog:data.sqlCatalog,groups:[],view:'list',runFilter:{},runsPageFull:false};
    window.previewCalls=[];window.previewNote='';
}
window.previewData=data;
window._flowWatchExecutionPane=()=>{};window._flowScheduleCatalogMonitor=()=>{};window._flowStopActivityMonitor=()=>{};window._flowRefreshActivity=()=>Promise.resolve();
window.toast=message=>{status(window.previewNote?`${message} · ${window.previewNote}`:message);window.previewNote='';};
window.navigate=async page=>{if(page==='flows')show('list');};
const record=(method,path,body)=>window.previewCalls.push({method,path,body:body?structuredClone(body):undefined});
const flowId=path=>Number(path.match(/\/api\/flows\/(\d+)/)?.[1]);

// --- the proposed control ------------------------------------------------------
const form=()=>document.querySelector('#flow-builder-form[data-source-type="python"]');
const editing=target=>data.flows.find(flow=>flow.id===Number(target.dataset.id))||null;
const folderMode=target=>target.querySelector('input[name="preview-folder-mode"]:checked')?.value||'none';
const runOnly=target=>target.querySelector('input[name="flow-python-mode"]:checked')?.value==='run';
function syncFolder(target){
    const mode=folderMode(target),existing=editing(target);
    const name=target.querySelector('#flow-name')?.value.trim()||'Flow name';
    const help=target.querySelector('#preview-folder-help');
    if(help)help.textContent=mode==='managed'
        ?`Metronome creates ${ROOT}\\Python\\${name} with Downloads and Scripts, keeps this Flow's files there and renames it with the Flow.`
        :existing?.managed_folder
            ?`Metronome stops using ${existing.folder_relative.replace(/\//g,'\\')}. The folder and its files stay on disk; the scripts keep running from where they are.`
            :runOnly(target)?'Nothing is created on disk for this Flow. The scripts run in place, from any folder.'
            :'Nothing is created on disk for this Flow. Choose the Output folder in the Output step.';
    const destination=target.querySelector('#flow-destination');
    if(!destination)return;
    if(mode==='none'){
        if(!destination.querySelector('#flow-target-folder')){
            const saved=existing&&!existing.managed_folder?existing.target_folder||'':'';
            destination.innerHTML=`<label><span>Output folder</span><input id="flow-target-folder" maxlength="2000" value="${window.esc(saved)}" placeholder="\\\\fileserver\\reports\\orders"><small>Run folders and the final file go here. Any folder the worker service can write, even while Enforce paths is on; prefer a \\\\server\\share path to a mapped drive.</small></label>`;
        }
        destination.querySelector('#flow-target-folder').required=!runOnly(target);
    }else if(destination.querySelector('#flow-target-folder')){
        destination.outerHTML=window._flowDestinationHtml(existing?.managed_folder?existing:null);
    }
}
function inject(){
    const target=form();
    if(!target||target.querySelector('#preview-folder-choice'))return;
    const modes=target.querySelector('.flow-python-mode');
    if(!modes)return;
    modes.insertAdjacentHTML('afterend',`<fieldset id="preview-folder-choice" class="flow-span-2 flow-python-mode"><legend>Flow folder <small>(proposed)</small></legend>
        <label class="flow-check"><input type="radio" name="preview-folder-mode" value="none"><span>No Metronome folder</span></label>
        <label class="flow-check"><input type="radio" name="preview-folder-mode" value="managed"><span>Managed Metronome folder</span></label>
        <p id="preview-folder-help" class="flow-dialog-help" role="status"></p></fieldset>`);
    const existing=editing(target);
    target.querySelector(`input[name="preview-folder-mode"][value="${existing?.managed_folder?'managed':'none'}"]`).checked=true;
    target.querySelectorAll('input[name="preview-folder-mode"],input[name="flow-python-mode"]').forEach(radio=>radio.addEventListener('change',()=>syncFolder(target)));
    target.querySelector('#flow-name')?.addEventListener('input',()=>syncFolder(target));
    syncFolder(target);
}
new MutationObserver(inject).observe(document.getElementById('flow-workspace'),{childList:true,subtree:true});

// --- fictional API ---------------------------------------------------------------
const validation=(field,message)=>Object.assign(Error(message),{validation:[{loc:['body',field],msg:message}]});
function folderChoice(body){
    const target=form();
    const managed=folderMode(target)==='managed';
    const folder=target.querySelector('#flow-target-folder')?.value.trim()||'';
    if(!managed&&body.python_run_mode!=='run'){
        if(!folder)throw validation('target_folder','Enter the Output folder for this Flow\'s files, or choose Managed Metronome folder.');
        if(!/^([A-Za-z]:[\\/]|\\\\)/.test(folder))throw validation('target_folder','Output folder must be a full path such as \\\\server\\share\\folder or D:\\Reports.');
    }
    return {managed,folder:managed?null:(body.python_run_mode==='run'?null:folder)};
}
function savedFlow(body,id,existing,choice){
    const name=body.name||existing?.name||'New flow';
    const note=choice.managed?`managed folder Python\\${name}`:existing?.managed_folder?`no Metronome folder; Python\\${existing.name} stays on disk`:'no Metronome folder';
    window.previewNote=`Preview · ${note}`;
    return {...(existing||pythonFlow(id,name)),...body,id,name,source_adapter:'python_script',managed_folder:choice.managed,
        flow_folder:choice.managed?`${ROOT}\\Python\\${name}`:null,folder_relative:choice.managed?`Python/${name}`:null,
        target_folder:choice.managed?`${ROOT}\\Python\\${name}\\Downloads`:choice.folder,
        owner_name:data.people.find(person=>person.id===body.owner_person_id)?.name||null,
        post_sql_refresh:body.post_sql_refresh||{mode:'off',views:[]},email_delivery:body.email_delivery||{...emailOff},
        last_run_at:existing?.last_run_at??null,last_status:existing?.last_status??null};
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
    if(path==='/api/flows/python/inspect')return {readable:true,path:body.path,interpreter:body.interpreter||'C:\\Windows\\py.exe',interpreter_reason:body.interpreter?'Flow setting':'py launcher',hint:null};
    if(path==='/api/flows'){
        const choice=folderChoice(body);
        const id=Math.max(0,...data.flows.map(flow=>flow.id))+1;
        const created=savedFlow(body,id,null,choice);data.flows.push(created);window._flowsState.flows=data.flows;window.previewPayload={...body,managed_folder:choice.managed,target_folder:choice.folder};
        return {...created,standalone:{state:'current'}};
    }
    throw Error('Preview only.');
};
window.apiPut=async(path,body)=>{
    record('PUT',path,body);
    const choice=folderChoice(body);
    const index=data.flows.findIndex(flow=>flow.id===flowId(path));if(index<0)throw Error('Flow not found.');
    data.flows[index]=savedFlow(body,flowId(path),data.flows[index],choice);window._flowsState.flows=data.flows;window.previewPayload={...body,managed_folder:choice.managed,target_folder:choice.folder};
    return {...data.flows[index],standalone:{state:'current'}};
};
window.apiPatch=async(path,body)=>{record('PATCH',path,body);const target=data.flows.find(flow=>flow.id===flowId(path));if(target)Object.assign(target,body);return {...target,...body};};
window.apiPostForm=async path=>{record('POST',path);throw Error('Preview only.');};
window.apiPost=async path=>{
    record('POST',path);
    if(path.endsWith('/open-folder')){
        // Proposed: a Flow without a folder says where its scripts run instead.
        const flow=data.flows.find(item=>item.id===flowId(path));
        if(!flow?.target_folder)throw Error(`${flow?.name} has no Metronome folder: its scripts run from ${flow?.python_scripts?.[0]?.replace(/\\[^\\]*$/,'')}.`);
        return {opened:true};
    }
    if(path.endsWith('/run')){status('Run queued. The scripts start in the signed-in Windows session, as they would from PowerShell.');return {id:90};}
    throw Error('Preview only.');
};
window.apiDelete=async path=>{record('DELETE',path);return {};};
function show(screen){
    document.querySelectorAll('.preview-nav button').forEach(button=>button.setAttribute('aria-current',String(button.dataset.screen===screen)));
    if(screen==='new')window._flowShowView('builder',{_source_type:'python'});
    else if(screen==='builder')window._flowShowView('builder',data.flows.find(flow=>flow.id===31));
    else if(screen==='list')window._flowShowView('list');
}
window.previewShow=show;
document.querySelectorAll('.preview-nav button').forEach(button=>button.onclick=()=>show(button.dataset.screen));
document.getElementById('preview-reset').onclick=()=>{reset();status('Preview reset.');show('new');};
reset();
show('new');
