/* Fictional APIs only. The editor, controls, typeface and styles are production assets. */
(() => {
    const locator=(method,text)=>[{method,args:[text],kwargs:{}}];
    const definition={version:2,timezone:'Asia/Dubai',parameters:{},identity_candidates:[],
        identity:{text:'Regional Orders',target:{page:'page',locator:locator('get_by_text','Regional Orders')}},
        readiness:{mode:'changed_text',trigger_step_id:'generate',target:{page:'page',locator:locator('locator','#status')}},
        steps:[
            {id:'open',action:'goto',page:'page',args:['https://example.test/orders'],label:'Open Regional Orders'},
            {id:'generate',action:'click',page:'page',locator:locator('get_by_text','Run report')},
            {id:'ready',action:'assert',assertion:'to_have_text',page:'page',locator:locator('locator','#status'),args:['Results updated']},
            {id:'download',action:'download',page:'page',steps:[{id:'download-click',action:'click',page:'page',locator:locator('get_by_text','Download Excel')}],output:{format:'xlsx',headers:[],allow_empty:false}}
        ]};
    const data={flow:{id:901,name:'Weekly regional orders',source_adapter:'gscm_portal',enabled:false},sessions:[],revisions:[{id:1,status:'draft',definition}]};
    const calls=[];
    let sequence=0;
    const current=()=>data.sessions[0];
    const transition=(status,progress={},error=null)=>{
        if(!current())return;
        Object.assign(current(),{status,progress_json:JSON.stringify(progress),error});
        data.revisions.find(r=>r.id===current().revision_id).status=status==='succeeded'?'validated':['failed','cancelled'].includes(status)?'draft':'validating';
    };
    const after=(delay,token,fn)=>setTimeout(()=>{if(token===sequence)fn();},delay);
    function play() {
        const token=++sequence,outcome=document.getElementById('startup-outcome').value;
        if(window.RECORDING_STARTUP_AUTOPLAY===false)return;
        if(outcome==='unavailable'){
            after(6500,token,()=>transition('failed',{},'The recording browser did not start. Try again. If it repeats, check Flow workers in System.'));
            return;
        }
        after(3500,token,()=>transition('claimed'));
        if(outcome==='failed')after(7000,token,()=>transition('failed',{},'The recording browser could not open. Try Test recording again.'));
        else {
            after(7000,token,()=>transition('running'));
            after(11000,token,()=>transition('succeeded'));
        }
    }
    window.api=async()=>structuredClone(data);
    window.apiPostJson=async(path,body)=>{
        calls.push({path,body:structuredClone(body)});
        if(path.endsWith('/revisions')){
            const id=data.revisions[0].id+1;
            data.revisions.unshift({id,status:'draft',definition:structuredClone(body.definition)});
            return {revision_id:id};
        }
        if(path.endsWith('/validate')){
            const id=Number(path.split('/').at(-2));
            data.sessions.unshift({scan_id:100+calls.length,revision_id:id,operation:'validate',status:'queued',progress_json:'{}'});
            data.revisions.find(r=>r.id===id).status='validating';
            play();
            return {revision_id:id,worker:{status:'starting'}};
        }
        throw Error('This preview only tests the saved recording.');
    };
    window.apiPost=async path=>{
        calls.push({path});
        if(path.endsWith('/cancel')){
            sequence++;
            transition('cancelled',{message:'Starting worker…'});
            return {};
        }
        throw Error('This preview only tests the saved recording.');
    };
    // Tests adjust only the fictional API response, then wait for real editor polling.
    window.RecordingStartupPreview={data,calls,transition};
    document.getElementById('startup-open').onclick=()=>RecordedFlowEditor.open(901);
    RecordedFlowEditor.open(901);
})();
