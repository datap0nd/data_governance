(() => {
 const config=window.FILM_CONFIG,cue=config.cues,L=config.layout,clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x)),ease=x=>1-Math.pow(1-clamp(x),3),lerp=(a,b,p)=>a+(b-a)*p;
 const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
 const scenes=config.scenes.map(s=>({...s,el:document.getElementById(s.id)})),at=id=>config.scenes.find(s=>s.id===id);
 const seg=config.narration;
 const reveal=(el,p,dy=14)=>{el.style.opacity=p;el.style.transform=`translateY(${(1-p)*dy}px)`;};
 const ROW=46,LOOP=14*ROW;
 // A shared pointer helper: keyframes are [time,x,y]; clicks pulse the ring.
 function pointer(el,t,path,clicks,from,to){
  const visible=t>=from&&t<to;el.style.visibility=visible?'inherit':'hidden';if(!visible)return;
  let a=path[0],b=path[0];for(let i=1;i<path.length;i++){b=path[i];if(t<=b[0])break;a=b;}
  let p=clamp((t-a[0])/Math.max(.001,b[0]-a[0]));p=p*p*(3-2*p);
  el.style.transform=`translate(${lerp(a[1],b[1],p)-4.3}px,${lerp(a[2],b[2],p)-3.25}px)`;
  const last=clicks.filter(c=>c<=t).at(-1)??-100,age=t-last;el.querySelector('.pointer').style.transform=`scale(${age>=0&&age<.14?.87:1})`;
  const ring=el.querySelector('i');if(ring){ring.style.opacity=age>=0&&age<.48?1-age/.48:0;ring.style.transform=`scale(${.4+clamp(age/.48)*1.3})`;}
 }

 // 1. Too many places to look.
 const portalCues=[cue.asap,cue.gscm,cue.bdp,cue.nerp],fileCues=[cue.excel,cue.emails,cue.presentations,cue.pdfs];
 // The hunt: each list scrolls to the report a person would need, then the pointer opens it.
 const huntStart=cue.look-.4,SLOT=2,hunt=[[cue.look+.15,0,'Sell-out by country'],[cue.opening+.1,1,'Sell-in by account'],[cue.opening+1.3,3,'Marketing spend'],[cue.piecing-.1,'file',0],[cue.piecing+1.2,2,'Device activations']];
 const rowIndex=(i,name)=>[...$$('[data-portal]')[i].querySelectorAll('.report-row span')].findIndex(s=>s.textContent===name)+14;
 const huntFor=i=>hunt.find(h=>h[1]===i);
 const scrollAt=(i,t)=>{const free=(Math.min(t,huntStart)*20+i*137)%LOOP,h=huntFor(i);if(!h)return free;
  const move=h[0]-1,target=(rowIndex(i,h[2])-SLOT)*ROW;return t<move?free:lerp((Math.min(move,huntStart)*20+i*137)%LOOP,target,ease((t-move)/.6));};
 const rowCenter=i=>[L.windows[i][0]+250,L.windows[i][1]+72+66+SLOT*ROW+23];
 function mess(t){
  const end=at('sMess').end,gather=ease((t-(end-.8))/.8);
  $$('[data-portal]').forEach((el,i)=>{
   const p=ease((t-.4-i*.45)/.6),focus=clamp(1-Math.abs(t-portalCues[i]-.7)/1.1);
   const dx=(960-(L.windows[i][0]+232))*gather,dy=(500-(L.windows[i][1]+280))*gather;
   el.style.opacity=p*(1-gather);el.style.transform=`translate(${dx}px,${dy+(1-p)*30}px) scale(${(1+focus*.025)*(1-gather*.6)})`;
   el.style.zIndex=focus>.05?10:i;el.style.boxShadow=focus>.05?`0 0 0 ${5*focus}px ${['#1d4f91','#6551a8','#0e7490','#9a3412'][i]}33,0 24px 50px #18233b26`:'';
   const n=el.querySelector('[data-count]');n.textContent=Math.round(+n.dataset.count*ease((t-portalCues[i]+.2)/1.3));
   const offset=scrollAt(i,t);el.querySelector('.report-scroll').style.transform=`translateY(${-offset}px)`;
   const rows=[...el.querySelectorAll('.report-row')],h=huntFor(i);rows.forEach(r=>r.classList.remove('hit'));
   if(h&&t>=h[0])rows[rowIndex(i,h[2])].classList.add('hit');
  });
  $$('[data-file]').forEach((el,i)=>{
   const p=ease((t-fileCues[i%4]-(i>3?.25:0)+.15)/.5),slot=L.files[i];
   const dx=(960-(slot[0]+165))*gather,dy=(500-(slot[1]+37))*gather;
   el.style.opacity=p*(1-gather);el.style.transform=`translate(${dx}px,${dy-(1-p)*40}px) rotate(${slot[2]*(1-gather)}deg) scale(${1-gather*.6})`;
   el.classList.toggle('hit',hunt.some(([when,w,target])=>w==='file'&&target===i&&t>=when));
  });
  const q=ease((t-seg[2].start+.1)/.5)*(1-gather);$('.hunt-question').style.opacity=q;$('.hunt-question').style.transform=`translate(-50%,${(1-q)*-12}px)`;
  const clock=ease((t-cue.look)/.4)*(1-gather);$('.hunt-clock').style.opacity=clock;
  const minutes=Math.round(240*clamp((t-cue.look)/(seg[2].end-cue.look)));$('.hunt-clock span').textContent=minutes<60?`${minutes} min`:`${Math.floor(minutes/60)} h ${String(minutes%60).padStart(2,'0')} min`;
  // Arrive, dwell, click, then move on.
  const path=[[huntStart,960,90],...hunt.flatMap(([when,w,target])=>{const [x,y]=w==='file'?[L.files[target][0]+60,L.files[target][1]+37]:rowCenter(w);return [[when-.3,x,y],[when+.2,x,y]];})];
  pointer($('.hunt-pointer'),t,path,hunt.map(h=>h[0]),huntStart,end-.8);
 }

 // 2. Everything comes together in Wizard.
 const scattered=[...L.windows.map(([x,y])=>[x+232,y+280]),...L.files.slice(0,4).map(([x,y])=>[x+165,y+37])];
 function join(t){
  const start=at('sJoin').start,gather=ease((t-cue.together+.5)/1.3);
  $$('[data-node]').forEach((el,i)=>{
   const [sx,sy]=scattered[i],[rx,ry]=L.ring[i],x=lerp(sx,rx,gather),y=lerp(sy,ry,gather);
   el.style.opacity=ease((t-start-i*.04)/.4);el.style.transform=`translate(${x-125}px,${y-42}px) scale(${lerp(.82,1,gather)})`;
  });
  const lines=ease((t-cue.together-.6)/.6);$$('[data-join-line]').forEach(el=>el.style.opacity=lines);
  $('.join-flow').style.strokeDashoffset=-t*70;
  const hub=ease((t-cue.meetWizard+.1)/.5),pulse=t>=cue.lives?Math.sin(clamp((t-cue.lives)/.8)*Math.PI)*.08:0;
  $('.join-hub').style.opacity=hub;$('.join-hub').style.transform=`scale(${.6+.4*hub+pulse})`;
  reveal($('.join-name'),ease((t-cue.meetWizard-.15)/.5));
  const ai=ease((t-cue.localAI+.1)/.45);$('.join-ai').style.opacity=ai;$('.join-ai').style.transform=`translate(-50%,${(1-ai)*12}px)`;
 }

 // 3. Wizard: ask, find, answer, trust.
 const question=config.question,typeStart=cue.ask+.35,typeEnd=typeStart+2.6,sent=typeEnd+.6;
 const lit={gscm:cue.sellOut,asap:cue.share,nerp:cue.spend,excel:cue.targets},foundDone=cue.targets+.7,dash=cue.seconds-.35;
 const bodyPoint=(sel,dx=0,dy=0)=>{const r=$(sel).getBoundingClientRect(),o=$('.app-body').getBoundingClientRect();return [r.left-o.left+r.width/2+dx,r.top-o.top+r.height/2+dy];};
 function app(t){
  const start=at('sApp').start,win=ease((t-start)/.6);$('.app-window').style.opacity=win;$('.app-window').style.transform=`translateY(${(1-win)*40}px) scale(${.98+.02*win})`;
  const home=$('.wz-home');home.style.opacity=1-ease((t-sent)/.3);home.style.visibility=t<sent+.3?'inherit':'hidden';
  $$('.home-mark,.home-hello,.wz-home>h1,.ask-box').forEach((el,i)=>reveal(el,ease((t-start-.25-i*.12)/.5)));
  $$('.home-sources .src-chip').forEach((el,i)=>reveal(el,ease((t-start-.8-i*.07)/.4),10));reveal($('.home-sources-label'),ease((t-start-.7)/.4),8);
  const typed=t>=sent?'':question.slice(0,Math.floor(clamp((t-typeStart)/(typeEnd-typeStart))*question.length));
  $('.ask-typed').textContent=typed;$('.ask-placeholder').style.display=typed?'none':'';
  const focused=t>=cue.ask-.3&&t<sent;$('.ask-box').classList.toggle('focused',focused);$('.ask-caret').style.opacity=focused&&Math.floor(t*2.5)%2===0?1:0;
  $('.ask-send').classList.toggle('ready',typed.length===question.length);$('.ask-send').classList.toggle('pressed',t>=sent-.1&&t<sent+.05);
  const send=bodyPoint('.ask-send');pointer($('.app-pointer'),t,[[typeEnd-.4,send[0]+160,send[1]+120],[sent-.25,send[0],send[1]],[sent+.6,send[0]-40,send[1]+140]],[sent-.1],typeEnd-.4,sent+.6);
  // The question bubble steps aside when the security card opens beneath the badge.
  reveal($('.wz-user'),ease((t-sent-.05)/.4)*(1-ease((t-cue.safe+.1)/.3)),10);
  // Finding the right reports.
  const find=$('.wz-find'),findIn=ease((t-sent-.2)/.4),findOut=ease((t-dash)/.35);find.style.opacity=findIn*(1-findOut);find.style.visibility=t>=sent&&t<dash+.4?'inherit':'hidden';
  $('.find-spinner').style.transform=`rotate(${t*360}deg)`;$('.find-spinner').style.opacity=t<foundDone?1:0;
  $('.find-title').textContent=t<foundDone?'Finding the right reports…':'Found 4 reports across 8 sources';
  $$('[data-tile]').forEach((el,i)=>{const key=el.dataset.tile,on=lit[key]!==undefined&&t>=lit[key];reveal(el,ease((t-sent-.35-i*.06)/.4),12);
   el.classList.toggle('lit',on);el.style.opacity=Math.min(+el.style.opacity,t>=foundDone&&!on?.45:1);
   el.querySelector('.tile-ok').style.opacity=on?ease((t-lit[key])/.3):0;
   const scan=t<foundDone&&!on;el.querySelector('.tile-scan').style.opacity=scan?1:0;el.querySelector('.tile-scan').style.transform=`translateX(${((t*1.4+i*.37)%1.6-.6)*280}px)`;});
  $$('[data-found]').forEach((el,i)=>reveal(el,ease((t-[cue.sellOut,cue.share,cue.spend,cue.targets][i]-.1)/.4),16));
  // The answer builds as it is named.
  const dashEl=$('.wz-dash');dashEl.style.opacity=ease((t-dash-.1)/.4);dashEl.style.visibility=t>=dash?'inherit':'hidden';
  reveal($('.dash-used'),ease((t-dash)/.4),8);reveal($('.dash-head'),ease((t-dash-.2)/.45),10);reveal($('.dash-summary'),ease((t-cue.summary+.2)/.5),10);
  $$('[data-kpi]').forEach((el,i)=>{reveal(el,ease((t-cue.numbers-i*.13)/.45),16);
   const hot=(i===0&&t>=cue.eight-.1&&t<cue.eight+1.6)||(i===1&&t>=cue.marketShare-.1&&t<cue.marketShare+1.8);el.classList.toggle('pulse',hot);});
  $$('.chart-card').forEach((el,i)=>reveal(el,ease((t-cue.charts-i*.2)/.5),18));
  const focusKsa=t>=cue.saudi-.1&&t<cue.checked;
  $$('[data-bar]').forEach((el,i)=>{const p=ease((t-cue.charts-.35-i*.1)/.7);el.querySelector('.bar').style.transform=`scaleY(${p})`;el.querySelector('.bar-value').style.opacity=clamp((p-.6)/.4);
   el.style.opacity=focusKsa&&i>0?.4:1;});
  $('.bar-callout').style.opacity=ease((t-cue.saudi)/.35);
  $$('[data-slice]').forEach((el,i)=>{const p=ease((t-cue.charts-.5)/1.1),len=+el.dataset.len,off=+el.dataset.off;
   el.setAttribute('stroke-dasharray',`${Math.max(0,Math.min(len,100*p-off))} 100`);
   const pop=i===0?ease((t-cue.marketShare+.1)/.4)*(1-ease((t-cue.checked)/.4)):0;el.style.transform=`scale(${1+pop*.07})`;});
  $$('[data-legend]').forEach((el,i)=>reveal(el,ease((t-cue.charts-.9-i*.12)/.4),8));
  // Trust: checked against sources, safe by design.
  $('.dash-checked').style.opacity=ease((t-cue.checked)/.4);
  $$('.kpi-src').forEach((el,i)=>el.style.opacity=ease((t-cue.checked-.2-i*.12)/.3));$$('.chart-src').forEach((el,i)=>el.style.opacity=ease((t-cue.checked-.6-i*.15)/.3));
  const glow=t>=cue.safe-.2?.5+.5*Math.sin((t-cue.safe)*4):0;$('.app-secure').style.boxShadow=t>=cue.safe-.2?`0 0 0 ${3+glow*4}px #10b98133`:'';
  reveal($('.secure-card'),ease((t-cue.safe)/.45),-10);
  $$('.secure-card>div').forEach((el,i)=>reveal(el,ease((t-[cue.authorized,cue.authorized+.6,cue.allowed+.4][i]+.1)/.35),6));
 }

 // 4. End card.
 function endCard(t){
  const start=at('sEnd').start;
  $$('[data-end-chip]').forEach((el,i)=>reveal(el,ease((t-start-.2-i*.08)/.45),12));
  const mark=ease((t-start-.1)/.6);$('.end-mark').style.opacity=mark;$('.end-mark').style.transform=`scale(${.8+.2*mark})`;
  const name=ease((t-cue.endWizard+.1)/.5);$('.end-lockup>b').style.opacity=name;$('.end-lockup>b').style.transform=`translateX(${(1-name)*-24}px)`;
  reveal($('.end-tagline'),ease((t-cue.oneAnswer+.5)/.6),12);
 }

 function draw(t){
  scenes.forEach(s=>{const active=t>=s.start&&t<s.end;s.el.style.visibility=active?'visible':'hidden';s.el.style.opacity=active?'1':'0';});
  mess(t);join(t);app(t);endCard(t);
 }
 window.Film={draw,seek:draw};window.__timelines=window.__timelines||{};const clock={t:0},timeline=gsap.timeline({paused:true});timeline.to(clock,{t:config.duration,duration:config.duration,ease:'none',onUpdate:()=>draw(clock.t)},0);window.__timelines['metronome-your-data-connected']=timeline;window.addEventListener('hf-seek',e=>draw(e.detail.time));draw(0);
})();
