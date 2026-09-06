/* Uses production components with fictional, in-memory settings and playback. */
if(document.readyState==='loading')await new Promise(resolve=>document.addEventListener('DOMContentLoaded',resolve,{once:true}));
for(const src of ['/static/app.js?v=69','/static/flow_recording_model.js?v=4','/static/flow_recording_editor.js?v=7']){
    await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=src;script.onload=resolve;script.onerror=reject;document.head.append(script);});
}
const button=name=>[{method:'get_by_role',args:['button'],kwargs:{name}}];
const initial={version:2,timezone:'Asia/Dubai',parameters:{},steps:[
    {id:'open',action:'goto',page:'page',locator:[],args:['https://reports.example.test/orders'],kwargs:{}},
    {id:'setting',action:'click',page:'page',label:'Click Setting',locator:button('Setting'),args:[],kwargs:{}},
    {id:'public',action:'click',page:'page',label:'Click Public',locator:button('Public'),args:[],kwargs:{}},
    {id:'run',action:'click',page:'page',locator:button('Run report'),args:[],kwargs:{}},
    {id:'download',action:'download',page:'page',locator:[],steps:[{id:'export',action:'click',page:'page',locator:button('Download Excel'),args:[],kwargs:{}}],output:{format:'xlsx'}}
]};
const settings={recording_wait_seconds:10,browser_channel:'chrome',total_capacity:12,headless_capacity:2,headed_capacity:2,slots:[],headed_slots:[],active_total:0,active_headless:0,active_headed:0,online_capacity:0,portals:[]};
const data={recording_wait_seconds:10,flow:{id:9002,name:'Regional orders',enabled:false},sessions:[],revisions:[{id:1,status:'draft',definition:structuredClone(initial),created_at:'2026-09-06T14:00:00Z'}]};
const calls=[],debugRequests=[];
window.playbackPreview={data,settings,calls,debugRequests};
window.apiHeaders=extra=>({'X-Client-Key':'fictional-preview',...extra});
window.api=async path=>structuredClone(path==='/api/system/flows'?settings:data);
window.apiPut=async(path,body)=>{
    calls.push({path,body:structuredClone(body)});
    if(window.previewFailSettings)throw Error('Settings could not be saved. Try again.');
    Object.assign(settings,body);data.recording_wait_seconds=settings.recording_wait_seconds;
    return structuredClone(settings);
};
window.apiPostJson=async(path,body)=>{
    calls.push({path,body:structuredClone(body)});
    if(path.endsWith('/validate')){
        const revision=data.revisions[0];revision.status='validated';
        const outcomes=Object.fromEntries(RecordedFlowModel.all(revision.definition.steps).map(step=>[step.id,{outcome:'completed',...(step.action==='click'?{confirmation:step.id==='setting'||step.id==='public'?'confirmed':'unconfirmed',message:step.id==='setting'?'Setting opened.':step.id==='public'?'Public selected.':'Click sent.'}:{})}]));
        data.sessions.unshift({scan_id:calls.length,revision_id:revision.id,operation:'validate',status:'succeeded',progress_json:JSON.stringify({step_outcomes:outcomes})});
        return {revision_id:revision.id};
    }
    const id=data.revisions[0].id+1;data.revisions.unshift({id,status:'draft',definition:structuredClone(body.definition),created_at:new Date().toISOString()});return {revision_id:id};
};
window.apiPost=async()=>{throw Error('This preview only demonstrates testing saved actions.');};
const nativeFetch=window.fetch.bind(window);
window.fetch=async(path,options)=>{
    const match=String(path).match(/^\/api\/flows\/9002\/recordings\/(\d+)\/debug$/);
    if(!match)return nativeFetch(path,options);
    debugRequests.push({path,headers:options?.headers});
    if(window.previewFailDebug)return new Response(JSON.stringify({detail:'Debug log is temporarily unavailable.'}),{status:503,headers:{'Content-Type':'application/json'}});
    if(window.previewDeferDebug)return new Promise(resolve=>window.finishPreviewDebug=resolve);
    return new Response(`Recording test ${match[1]}\nDefault wait: ${settings.recording_wait_seconds} seconds\n1. Open report page — completed\n2. Click Setting — confirmed: Setting opened.\n3. Click Public — confirmed: Public selected.\n4. Click Run report — click sent; transition unconfirmed\n5. Download Excel — completed\n`,{headers:{'Content-Type':'text/plain'}});
};
window.toast=message=>document.getElementById('preview-status').textContent=message;
const workspace=document.getElementById('flow-workspace');
function recording(){
    workspace.querySelector('.flow-recording-page')?.close?.();
    workspace.innerHTML='<section id="flow-builder-form" class="flow-recording-page"><h1>Regional orders</h1><button class="btn-primary" id="preview-open">Recording</button></section>';
    document.getElementById('preview-open').onclick=()=>RecordedFlowEditor.open(data.flow.id,{name:data.flow.name});
    document.getElementById('preview-open').click();
}
async function generalSettings(){
    workspace.querySelector('.flow-recording-page')?.close?.();
    workspace.innerHTML='<button class="btn-secondary" id="preview-back">Back to recording</button>'+_flowCapacityHtml(settings);
    document.getElementById('preview-back').onclick=recording;
    bindFlowSettingsPage();
}
window.navigate=async page=>page==='flow-settings'?generalSettings():recording();
document.getElementById('preview-settings').onclick=generalSettings;
document.getElementById('preview-reset').onclick=()=>location.reload();
recording();
