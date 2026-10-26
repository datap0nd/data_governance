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
 // Lists drift, then rush past on "packed with reports" so the volume reads without a number.
 const travel=t=>{t=Math.min(t,huntStart);return t*20+140*clamp(t-cue.packed+.2,0,1.8);};
 const scrollAt=(i,t)=>{const free=(travel(t)+i*137)%LOOP,h=huntFor(i);if(!h)return free;
  const move=h[0]-1,target=(rowIndex(i,h[2])-SLOT)*ROW;return t<move?free:lerp((travel(move)+i*137)%LOOP,target,ease((t-move)/.6));};
 const rowCenter=i=>[L.windows[i][0]+250,L.windows[i][1]+72+SLOT*ROW+23];
 function mess(t){
  const end=at('sMess').end,gather=ease((t-(end-.8))/.8);
  $$('[data-portal]').forEach((el,i)=>{
   const p=ease((t-.4-i*.45)/.6),focus=clamp(1-Math.abs(t-portalCues[i]-.7)/1.1);
   const dx=(960-(L.windows[i][0]+232))*gather,dy=(500-(L.windows[i][1]+280))*gather;
   el.style.opacity=p*(1-gather);el.style.transform=`translate(${dx}px,${dy+(1-p)*30}px) scale(${(1+focus*.025)*(1-gather*.6)})`;
   el.style.zIndex=focus>.05?10:i;el.style.boxShadow=focus>.05?`0 0 0 ${5*focus}px ${['#1d4f91','#6551a8','#0e7490','#9a3412'][i]}33,0 24px 50px #18233b26`:'';
   const offset=scrollAt(i,t);el.querySelector('.report-scroll').style.transform=`translateY(${-offset}px)`;
   const sought=huntFor(i)?ease((t-huntFor(i)[0]+1)/.6):0;el.querySelector('.list-thumb').style.transform=`translateY(${8+clamp(travel(t)/700)*110+sought*24}px)`;
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

 // 3. The real Wizard app. The camera keeps a page point (fx, fy) at the frame centre at zoom z; near z = 1 the window
 // eases back to the centre, and once it is wider than the frame it never shows its edge.
 const clips=config.screen.clips,ids=Object.keys(clips),smooth=x=>x*x*(3-2*x);
 const camera=(keys,t)=>{let i=0;while(i<keys.length-1&&t>=keys[i+1][0])i++;const a=keys[i],b=keys[Math.min(i+1,keys.length-1)];
  const p=b===a||t<=a[0]?0:smooth(clamp((t-a[0])/(b[0]-a[0])));return [lerp(a[1],b[1],p),lerp(a[2],b[2],p),lerp(a[3],b[3],p)];};
 const place=(fx,fy,z)=>{const w=clamp((z-1)/.3);let x=lerp(960-800*z,960-fx*z,w),y=lerp(540-450*z,540-fy*z,w);
  if(1600*z>=1920)x=clamp(x,1920-1600*z,0);if(900*z>=1080)y=clamp(y,1080-900*z,0);return [x,y];};
 // Capture time at a film time, from the clip's anchors: drives the timelapse clock.
 const speedAt=(c,t)=>{const a=c.anchors;for(let i=0;i<a.length-1;i++)if(t>=a[i][0]&&t<a[i+1][0])return (a[i+1][1]-a[i][1])/(a[i+1][0]-a[i][0]);return 1;};
 const sourceAt=(c,t)=>{const a=c.anchors;let i=0;while(i<a.length-2&&t>=a[i+1][0])i++;const [f0,s0]=a[i],[f1,s1]=a[i+1];return s0+(s1-s0)*clamp((t-f0)/Math.max(.001,f1-f0));};
 // Hand-offs between windows: the first grows out of the hub logo; the second question slides in from the right;
 // the check slides back in from the left, returning to the first answer.
 const slide=(t,from)=>ease((t-from)/.6),T1=clips.stock.start+.2,T2=clips.check.start+.05;
 function screen(t){
  const start=at('sApp').start,end=at('sApp').end;
  const grow=ease((t-start)/.75);
  $('.hub-echo').style.opacity=t<start+.6?1-grow:0;$('.hub-echo').style.transform=`scale(${1+grow*2.2})`;
  const offset={ask:-2050*slide(t,T1),stock:2050*(1-slide(t,T1))+2050*slide(t,T2),check:-2050*(1-slide(t,T2))};
  ids.forEach(id=>{
   const c=clips[id],el=$(`[data-clip="${id}"]`),on=t>=c.start&&t<c.end;el.style.visibility=on?'inherit':'hidden';if(!on)return;
   const [fx,fy,z]=camera(c.camera,t);let [x,y]=place(fx,fy,z),k=1;
   if(id==='ask'){k=lerp(.18,1,grow);x=960+(x-960)*k;y=540+(y-540)*k+(1-grow)*-40;el.style.opacity=clamp(grow*1.6);}
   if(id==='check'){const out=ease((t-(end-.55))/.55);k=1-out*.08;x=960+(x-960)*k;y=540+(y-540)*k;el.style.opacity=1-out;}
   el.style.transform=`translate(${x+offset[id]}px,${y}px) scale(${z*k})`;
  });
  // Timelapse, only while Wizard works: the pill and a blue frame edge stay up for exactly that stretch. The pill shows
  // how many times faster than real time it plays and the real time the run has taken, counting at that speed.
  const tl=ids.map(id=>clips[id]).find(c=>t>=c.timelapse.from&&t<c.timelapse.to+.3);
  const on=tl?ease((t-tl.timelapse.from)/.25)*(1-ease((t-tl.timelapse.to)/.3)):0,pill=$('.tl-pill');
  pill.style.opacity=on;$('.tl-edge').style.opacity=on;
  if(tl){const at=Math.min(t,tl.timelapse.to-.001),real=Math.max(0,sourceAt(tl,at)-tl.timelapse.clockFrom);
   $('.tl-clock').textContent=`${Math.floor(real/60)}:${String(Math.floor(real%60)).padStart(2,'0')}`;
   $('.tl-speed').textContent=`${Math.round(speedAt(tl,at))}×`;
   pill.style.transform=`translate(-50%,${(1-ease((t-tl.timelapse.from)/.3))*-16}px)`;
   pill.querySelector('.lu').style.opacity=.55+.45*Math.abs(Math.sin((t-tl.timelapse.from)*5));}
  reveal($('.real-tag'),ease((t-start-.9)/.5)*(1-ease((t-end+.6)/.4)),-8);
  // Safe: the app steps back and the security card opens on "safe".
  const dim=ease((t-cue.safe+.15)/.45)*(1-ease((t-end+.5)/.4));$('.screen-dim').style.opacity=dim*.82;
  const card=$('.safe-card'),cp=ease((t-cue.safe)/.45)*(1-ease((t-end+.5)/.4));card.style.opacity=cp;card.style.transform=`translate(-50%,calc(-50% + ${(1-cp)*18}px)) scale(${.97+.03*cp})`;
  $$('.safe-card>div').forEach((el,i)=>reveal(el,ease((t-[cue.authorized,cue.authorized+.7,cue.allowed+.5][i]+.1)/.35),8));
 }

 // 4. End card: Wizard's logo, the tagline, every source.
 function endCard(t){
  const start=at('sEnd').start;
  const logo=ease((t-start-.15)/.7),pulse=t>=cue.endWizard?Math.sin(clamp((t-cue.endWizard)/.6)*Math.PI)*.025:0;
  $('.end-lockup').style.opacity=logo;$('.end-lockup').style.transform=`translateY(${(1-logo)*18}px) scale(${.94+.06*logo+pulse})`;
  reveal($('.end-tagline'),ease((t-cue.oneAnswer+.5)/.6),12);
  $$('[data-end-chip]').forEach((el,i)=>reveal(el,ease((t-cue.oneAnswer-.1-i*.07)/.45),12));
 }

 function draw(t){
  scenes.forEach(s=>{const active=t>=s.start&&t<s.end;s.el.style.visibility=active?'visible':'hidden';s.el.style.opacity=active?'1':'0';});
  mess(t);join(t);screen(t);endCard(t);
 }
 window.Film={draw,seek:draw};window.__timelines=window.__timelines||{};const clock={t:0},timeline=gsap.timeline({paused:true});timeline.to(clock,{t:config.duration,duration:config.duration,ease:'none',onUpdate:()=>draw(clock.t)},0);window.__timelines['metronome-your-data-connected']=timeline;window.addEventListener('hf-seek',e=>draw(e.detail.time));draw(0);
})();
