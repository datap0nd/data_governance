/* Fictional, in-memory preview. No worker, portal, file or application requests. */
(() => {
    const button=name=>[{method:'get_by_role',args:['button'],kwargs:{name}}];
    const initial={version:2,timezone:'Asia/Dubai',parameters:{},steps:[
        {id:'open',action:'goto',page:'page',locator:[],args:['https://reports.example.test/orders'],kwargs:{}},
        {id:'run',action:'click',page:'page',locator:button('Run report'),args:[],kwargs:{}},
        {id:'download',action:'download',page:'page',locator:[],steps:[{id:'export',action:'click',page:'page',locator:button('Download Excel'),args:[],kwargs:{}}],output:{format:'xlsx'}}
    ]};
    const data={flow:{id:9001,name:'Regional orders',source_adapter:'gscm_portal',enabled:false},sessions:[],
        revisions:[{id:1,status:'draft',definition:structuredClone(initial),created_at:'2026-09-06T12:00:00Z'}]};
    const calls=[];
    window.optionalPreview={data,calls};
    window.api=async()=>structuredClone(data);
    window.apiPostJson=async(path,body)=>{
        calls.push({path,body:structuredClone(body)});
        if(path.endsWith('/validate')){
            const revision=data.revisions[0],download=revision.definition.steps.find(s=>s.action==='download');
            const minimum=download?.output?.min_rows||0,failed=minimum>3;
            const message=failed?`Downloaded 3 data rows; this check requires at least ${minimum}.`:'';
            revision.status=failed?'draft':'validated';
            const outcomes=Object.fromEntries(RecordedFlowModel.all(revision.definition.steps).map(s=>[s.id,{outcome:'completed'}]));
            if(failed)outcomes[download.id]={outcome:'failed',message};
            data.sessions.unshift({scan_id:calls.length,revision_id:revision.id,operation:'validate',status:failed?'failed':'succeeded',error:message,progress_json:JSON.stringify({step_outcomes:outcomes})});
            return {revision_id:revision.id};
        }
        const id=data.revisions[0].id+1;
        data.revisions.unshift({id,status:'draft',definition:structuredClone(body.definition),created_at:new Date().toISOString()});
        return {revision_id:id};
    };
    window.apiPost=async path=>{
        calls.push({path});
        if(path.endsWith('/start'))data.sessions.unshift({scan_id:calls.length,revision_id:data.revisions[0].id,operation:'record',status:'running',progress_json:JSON.stringify({stage:'recording',message:'Recording fictional actions…'})});
        if(path.endsWith('/finish')){
            const id=data.revisions[0].id+1;
            data.revisions.unshift({id,status:'draft',definition:structuredClone(initial),created_at:new Date().toISOString()});
            data.sessions[0].status='succeeded';
        }
        if(path.endsWith('/cancel'))data.sessions[0].status='cancelled';
        return {};
    };
    document.getElementById('preview-reset').onclick=()=>location.reload();
    document.getElementById('preview-open').onclick=()=>RecordedFlowEditor.open(data.flow.id,{name:data.flow.name});
    document.getElementById('preview-open').click();
})();
