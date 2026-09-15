// Use the production editor and tokens, with fictional in-memory API responses.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js', '/static/app.js', '/static/flow_recordings.js']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const catalog={sites:[{id:1,name:'GSCM',adapter:'gscm_portal',enabled:true}],reports:[{id:1,site_id:1,name:'Regional Orders',enabled:true,filters:[],automation:{},report_url:'https://example.test/report'}],asap_download_types:[]};
const saved={id:7,name:'Regional orders',source_type:'portal',site_id:1,report_id:1,execution_method:'recorded',flow_folder:'GSCM/Regional orders (id 7)',folder_relative:'GSCM/Regional orders (id 7)',target_folder:null,output_mode:'direct_replace',filename_template:'orders_{export}.xlsx',period_strategy:'none',file_format:'xlsx',schedule_type:'manual',browser_mode:'headed',selections:{}};
window._flowsState={catalog,flows:[saved],people:[],runs:[],scans:[],workers:[],estimates:{},sqlCatalog:{configured:false,targets:[],missing:[]}};
window._flowWatchExecutionPane=window._flowScheduleCatalogMonitor=window._flowStopActivityMonitor=()=>{};
window.toast=message=>document.getElementById('preview-status').textContent=message;
window.api=window.apiPost=async()=>{throw Error('Fictional preview only.');};
let revision=1;
let scriptName=saved.name, scriptMode=saved.browser_mode;
function files(){document.getElementById('preview-files').textContent=`metronome/flows/${saved.folder_relative}/\n  Downloads/\n    orders.xlsx (preserved)\n  Scripts/\n    run_flow.py\n    README.md\n    requirements.txt\n    versions/\n  flow.json`;document.getElementById('preview-script').textContent=`Python revision ${revision}: ${scriptName}; ${scriptMode} browser. ${saved.standalone?.state==='error'?'Files need attention; restore access and save again.':'Files match the saved settings.'}`;}
window.apiPostJson=window.apiPut=async(url,body)=>{
    const scenario=document.getElementById('preview-scenario').value;
    if(scenario==='collision')throw Error('A folder with this name already exists. Choose a different flow name and save again.');
    if(scenario==='unavailable')throw Error('Could not rename the flow folder. Restore folder access and save again. Your changes are kept here.');
    Object.assign(saved,body);
    const name=body.name.replace(/[<>:"/\\|?*\x00-\x1f]/g,'').replace(/\s+/g,' ').trim().replace(/[. ]+$/,'').slice(0,72)||'Flow';
    saved.flow_folder=saved.folder_relative=`GSCM/${name}`;
    saved.standalone=scenario==='script'?{state:'error',message:'Restore folder access and save again to refresh the Python script.'}:{state:'current'};
    if(scenario!=='script'){revision++;scriptName=saved.name;scriptMode=saved.browser_mode;}
    files();return saved;
};
window.navigate=async()=>{document.getElementById('flow-workspace').replaceChildren();};
window.RecordedFlowEditor={open:async()=>toast('Recording controls are outside this folder naming review.')};
document.getElementById('preview-edit').onclick=()=>{toast('');window._flowShowView('builder',saved);};
files();window._flowShowView('builder',saved);
