(() => {
  const root = document.querySelector('.builder-browser');
  if (!root) return;
  const $ = s => root.querySelector(s);
  const $$ = s => [...root.querySelectorAll(s)];
  const clamp = x => Math.max(0, Math.min(1, x));
  const placeholders = {data:'Choose a report', destination:'Choose a destination', schedule:'Choose when to run'};
  const input = $('#flow-website'), create = $('.create-flow');
  let state, lastTime = -Infinity, eventIndex = 0, actionClock = 0, replaying = false;
  const fresh = () => ({website:'', data:'', destination:'', schedule:'', open:null, createdAt:null});
  const ready = () => state.website && state.data && state.destination && state.schedule;
  function paint() {
    for (const key of ['data','destination','schedule']) {
      const button = $(`[data-dropdown="${key}"]`), menu = $(`#${key}-options`);
      button.querySelector('span').textContent = state[key] || placeholders[key];
      button.setAttribute('aria-expanded', String(state.open === key));
      menu.hidden = state.open !== key;
      button.closest('.build-field').classList.toggle('is-open',state.open === key);
      button.closest('.build-field').classList.toggle('has-value',Boolean(state[key]));
      $$(`[data-option="${key}"]`).forEach(option => option.setAttribute('aria-selected',String(option.dataset.value === state[key])));
    }
    // Open menus intentionally cover only the neighboring form text beneath them.
    // Clear the local audit markers again as soon as the menu closes.
    const covered = {data:['destination','schedule'],destination:['schedule'],schedule:['data','destination']}[state.open] || [];
    for (const key of ['website','data','destination','schedule']) {
      $$(`[data-field="${key}"] > div:first-child label, [data-field="${key}"] > .field-trigger > span, [data-field="${key}"] > .field-trigger > em`).forEach(el => {
        el.toggleAttribute('data-layout-allow-overlap',covered.includes(key));
        el.toggleAttribute('data-layout-allow-occlusion',covered.includes(key));
      });
    }
    create.toggleAttribute('data-layout-allow-overlap',state.open==='data');
    create.toggleAttribute('data-layout-allow-occlusion',state.open==='data');
    $('[data-field="website"]').classList.toggle('has-value',Boolean(state.website));
    create.disabled = !ready() || state.createdAt !== null;
    create.classList.toggle('is-submitted',state.createdAt !== null);
  }
  function pipeline(t) {
    const age = state.createdAt === null ? -100 : t-state.createdAt;
    $$('.pipeline-node').forEach((el,i) => {
      const p=clamp((age-.45-i*.28)/.45);
      el.style.opacity=p;el.style.transform=`translateY(${(1-p)*15}px)`;
    });
    $$('.node-link').forEach((el,i)=>el.style.opacity=clamp((age-.8-i*.28)/.4));
    $('.pipeline-status').style.opacity=clamp((age-1.6)/.4);
    $('.pipeline-empty').style.opacity=state.createdAt===null?'.22':'0';
    create.textContent=age>=1.9?'Flow created ✓':state.createdAt!==null?'Creating…':'Create Flow';
  }
  input.addEventListener('input',()=>{state.website=input.value;paint();});
  $$('[data-dropdown]').forEach(button=>button.addEventListener('click',()=>{
    const key=button.dataset.dropdown;
    state.open=state.open===key?null:key;paint();
  }));
  $$('[data-option]').forEach(button=>button.addEventListener('click',()=>{
    state[button.dataset.option]=button.dataset.value;state.open=null;paint();
  }));
  create.addEventListener('click',()=>{
    if(!ready()||state.createdAt!==null)return;
    state.createdAt=actionClock-(replaying?0:2.5);state.open=null;
    paint();pipeline(actionClock);
  });
  function reset() {
    state=fresh();input.value='';eventIndex=0;
    $$('.menu-options').forEach(menu=>menu.scrollTop=0);
    paint();pipeline(0);
  }
  // These are real control events, replayed deterministically for video seeks.
  const events=[
    {at:42.25,type:'focus',selector:'#flow-website'},
    {at:45.55,type:'click',selector:'[data-dropdown="data"]'},
    {at:48.05,type:'click',selector:'[data-option="data"][data-option-index="4"]'},
    {at:49.2,type:'click',selector:'[data-dropdown="destination"]'},
    {at:50.75,type:'click',selector:'[data-option="destination"][data-option-index="2"]'},
    {at:51.55,type:'click',selector:'[data-dropdown="schedule"]'},
    {at:52.9,type:'click',selector:'[data-option="schedule"][data-option-index="0"]'},
    {at:55.4,type:'click',selector:'.create-flow'}
  ];
  // Press first, then dispatch the control's click on release.
  events.forEach(event=>event.at+=.12);
  let typedAt=42.5;
  const address='asap.example',cadence=[.11,.16,.1,.19,.26,.12,.09,.18,.13,.15,.1,.19];
  [...address].forEach((_,i)=>{typedAt+=cadence[i];events.push({at:typedAt,type:'input',value:address.slice(0,i+1)});});
  events.sort((a,b)=>a.at-b.at);
  const clickTimes=[42.25,45.55,48.05,49.2,50.75,51.55,52.9,55.4];
  // Fixed composition coordinates are pointer-tip targets, in the app body.
  const path=[
    [41.4,1000,530],[42.08,260,163],[44.95,260,163],
    [45.4,400,273],[45.75,400,273],[46.12,215,336],[46.48,215,336],
    [46.78,235,368],[47.1,235,368],[47.75,245,464],[48.18,245,464],
    [49.04,400,382],[49.4,400,382],[50.2,220,495],[50.6,230,539],[50.88,230,539],
    [51.38,402,491],[51.74,402,491],[52.65,230,265],[53.06,230,265],
    [54.98,398,579],[55.75,398,579],[56.6,612,520],[59.8,612,520]
  ];
  function cursor(t) {
    let from=path[0],to=path[0];
    for(let i=1;i<path.length;i++){to=path[i];if(t<=to[0])break;from=to;}
    let p=clamp((t-from[0])/Math.max(.001,to[0]-from[0]));p=p*p*(3-2*p);
    const dx=to[1]-from[1],dy=to[2]-from[2],distance=Math.hypot(dx,dy)||1;
    const bend=Math.sin(Math.PI*p)*Math.min(18,distance*.035);
    const x=from[1]+dx*p-dy/distance*bend,y=from[2]+dy*p+dx/distance*bend;
    $('.flow-cursor').style.transform=`translate(${x-4.3}px,${y-3.25}px)`;
    $('.flow-cursor').style.visibility=t>=41.4&&t<58.5?'inherit':'hidden';
    const lastClick=clickTimes.filter(at=>at<=t).at(-1)??-100,age=t-lastClick;
    const down=age>=0&&age<.14;
    $('.flow-cursor .pointer').style.transform=`scale(${down?.87:1})`;
    const ring=$('.flow-cursor>i');ring.style.opacity=age>=0&&age<.48?1-age/.48:0;ring.style.transform=`scale(${.4+clamp(age/.48)*1.3})`;
    create.classList.toggle('pressed',t>=55.4&&t<55.54);
    create.classList.toggle('hovered',t>=54.98&&t<55.4);
    $$('[data-option]').forEach(button=>button.classList.remove('film-hover'));
    const hovered=t>=46.05&&t<46.65?'[data-option="data"][data-option-index="0"]':t>=46.7&&t<47.2?'[data-option="data"][data-option-index="1"]':t>=47.65&&t<48.05?'[data-option="data"][data-option-index="4"]':t>=50.12&&t<50.45?'[data-option="destination"][data-option-index="1"]':t>=50.55&&t<50.75?'[data-option="destination"][data-option-index="2"]':t>=52.55&&t<52.9?'[data-option="schedule"][data-option-index="0"]':null;
    if(hovered)$(hovered).classList.add('film-hover');
    // Open menus intentionally cover only the neighboring form text beneath them.
    // Clear the local audit markers again as soon as the menu closes.
    const covered = {data:['destination','schedule'],destination:['schedule'],schedule:['data','destination']}[state.open] || [];
    for (const key of ['website','data','destination','schedule']) {
      $$(`[data-field="${key}"] > div:first-child label, [data-field="${key}"] > .field-trigger > span, [data-field="${key}"] > .field-trigger > em`).forEach(el => {
        el.toggleAttribute('data-layout-allow-overlap',covered.includes(key));
        el.toggleAttribute('data-layout-allow-occlusion',covered.includes(key));
      });
    }
    create.toggleAttribute('data-layout-allow-overlap',state.open==='data');
    create.toggleAttribute('data-layout-allow-occlusion',state.open==='data');
    $('[data-field="website"]').classList.toggle('is-typing',t>=42.25&&t<44.8);
    $('.typing-caret').dataset.value=input.value;
    $('.typing-caret').style.opacity=t>=42.25&&t<44.8?(Math.floor(t*2.5)%2?'0':'1'):'0';
  }
  function draw(t) {
    if(t<lastTime)reset();
    replaying=true;
    while(eventIndex<events.length&&events[eventIndex].at<=t){
      const event=events[eventIndex++];actionClock=event.at;
      if(event.type==='input'){input.value=event.value;input.dispatchEvent(new Event('input',{bubbles:true}));}
      else if(event.type==='click')$(event.selector).click();
      else $(event.selector).focus({preventScroll:true});
    }
    replaying=false;actionClock=t;lastTime=t;pipeline(t);cursor(t);
  }
  reset();
  window.FilmBuilder={draw,reset:()=>{reset();lastTime=-Infinity;},getState:()=>({...state})};
})();
