/* Fictional, in-memory range-authoring preview. No portal or worker requests. */
(() => {
    const week=value=>({id:`week-${value}`,action:'click',page:'page',locator:[
        {method:'locator',args:['#weekly-periods'],kwargs:{}},
        {method:'get_by_role',args:['button'],kwargs:{name:value,exact:true}}
    ],args:[],kwargs:{}});
    const initial={version:2,timezone:'Asia/Dubai',parameters:{},steps:[
        {id:'open',action:'goto',page:'page',locator:[],args:['https://reports.example.test/orders'],kwargs:{}},
        week('2026-W32'),week('2026-W33'),week('2026-W34'),
        {id:'download',action:'download',page:'page',locator:[],steps:[{id:'export',action:'click',page:'page',locator:[{method:'get_by_role',args:['button'],kwargs:{name:'Download Excel'}}],args:[],kwargs:{}}],output:{format:'xlsx'}}
    ]};
    const data={flow:{id:9100,name:'Weekly orders',source_adapter:'gscm_portal',enabled:false},sessions:[],
        revisions:[{id:1,status:'draft',definition:structuredClone(initial),created_at:'2026-09-09T12:00:00Z'}]};
    const calls=[];
    window.rangePreview={data,calls};
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
