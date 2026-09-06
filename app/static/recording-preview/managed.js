// Load after DOMContentLoaded so the application bootstrap cannot run.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/app.js', '/static/flow_recordings.js']) {
    await new Promise((resolve, reject) => {const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const catalog={sites:[{id:1,name:'GSCM',adapter:'gscm_portal',enabled:true},{id:2,name:'ASAP',adapter:'asap_portal',enabled:true}],reports:[{id:1,site_id:1,name:'Regional Orders',enabled:true,filters:[],automation:{},report_url:'https://example.test/report'}],asap_download_types:[{key:'excel',file_format:'xlsx',preferred_suffix:'.xlsx',label:'Excel workbook'}]};
const saved={id:7,name:'Regional orders',source_type:'portal',site_id:1,report_id:1,execution_method:'recorded',flow_folder:'GSCM/Regional orders (id 7)',folder_relative:'GSCM/Regional orders (id 7)',target_folder:null,output_mode:'direct_replace',filename_template:'orders_{export}.xlsx',period_strategy:'none',file_format:'xlsx',schedule_type:'manual',browser_mode:'headed',selections:{}};
window._flowsState={catalog,flows:[saved],people:[],runs:[],scans:[],workers:[],estimates:{},sqlCatalog:{configured:false,targets:[],missing:[]}};
window._flowWatchExecutionPane=()=>{};
window._flowScheduleCatalogMonitor=()=>{};
window._flowStopActivityMonitor=()=>{};
window.toast=message=>document.getElementById('preview-status').textContent=message;
window.api=async()=>{throw Error('Preview only. No live API requests.');};
window.apiPost=async url=>{if(url.endsWith('/open-folder')){toast('Output: GSCM / Regional orders (id 7) / Downloads');return {opened:true};}throw Error('Preview only.');};
window.apiPostJson=window.apiPut=async(url,body)=>{window.previewPayload=body;if(window.previewFail)throw Error('Save failed. Your changes are kept.');Object.assign(saved,body);toast('Saved in preview');return saved;};
window.navigate=async()=>{};
window.RecordedFlowEditor={open:async(id,settings)=>{window.previewRecordingSettings=settings;toast('Recording opens here with the name and settings from Edit Flow.');}};
window._flowShowView('builder',saved);
document.getElementById('preview-new').onclick=()=>window._flowShowView('builder',{site_id:1,execution_method:'recorded'});
