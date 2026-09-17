/* Fictional, in-memory date range control preview. No portal or worker requests. */
(() => {
    const handle={id:'week-handle',action:'click',page:'page',locator:[
        {method:'locator',args:['#week-prompt'],kwargs:{}},
        {method:'get_by_role',args:['slider'],kwargs:{name:'Week end'}}
    ],args:[],kwargs:{}};
    const initial={version:3,timezone:'Asia/Dubai',parameters:{},steps:[
        {id:'open',action:'goto',page:'page',locator:[],args:['https://asap.example.test/sellout'],kwargs:{}},
        handle,
        {id:'download',action:'download',page:'page',locator:[],steps:[{id:'export',action:'click',page:'page',locator:[{method:'get_by_role',args:['button'],kwargs:{name:'Export'}}],args:[],kwargs:{}}],output:{format:'xlsx'}}
    ]};
    const data={flow:{id:9200,name:'Weekly sell-out',source_adapter:'asap_portal',enabled:false},sessions:[],
        revisions:[{id:1,status:'draft',definition:structuredClone(initial),created_at:'2026-09-17T12:00:00Z'}]};
    const calls=[];
    window.sliderPreview={data,calls};
    window.api=async()=>structuredClone(data);
    window.apiPostJson=async(path,body)=>{
        calls.push({path,body:structuredClone(body)});
        if(path.endsWith('/validate')){
            const revision=data.revisions[0];revision.status='validated';
            data.sessions.unshift({scan_id:calls.length,revision_id:revision.id,operation:'validate',status:'succeeded',progress_json:JSON.stringify({step_outcomes:{}})});
            return {revision_id:revision.id};
        }
        const id=data.revisions[0].id+1;
        data.revisions.unshift({id,status:'draft',definition:structuredClone(body.definition),created_at:new Date().toISOString()});
        return {revision_id:id};
    };
    window.apiPost=async path=>{calls.push({path});return {}};
    document.getElementById('preview-open').onclick=()=>RecordedFlowEditor.open(data.flow.id,{name:data.flow.name});
    document.getElementById('preview-open').click();
})();
