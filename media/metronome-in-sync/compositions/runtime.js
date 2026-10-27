(() => {
 const config=window.FILM_CONFIG,cue=config.cues,L=config.layout,clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x)),ease=x=>1-Math.pow(1-clamp(x),3),lerp=(a,b,p)=>a+(b-a)*p;
 const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
 const scenes=config.scenes.map(s=>({...s,el:document.getElementById(s.id)})),at=id=>config.scenes.find(s=>s.id===id);
 const seg=config.narration;
 const reveal=(el,p,dy=14)=>{el.style.opacity=p;el.style.transform=`translateY(${(1-p)*dy}px)`;};
 const M=config.motion,DT=1/30,smooth=x=>{x=clamp(x);return x*x*(3-2*x);},whip=x=>{x=clamp(x);return x<.5?16*x**5:1-Math.pow(2-2*x,5)/2;};
 // Kinetic type: each letter pops in larger, nudged right and soft, then settles, one after another.
 const kinetic=(el,t,from,{gap=.028,dur=.42,grow=.75,shift=16,soft=7}={})=>{
  [...el.querySelectorAll('.kc:not(.ks)')].forEach((c,i)=>{const q=1-ease((t-from-i*gap)/dur);
   c.style.opacity=clamp((1-q)*2.4);c.style.transform=q>0?`translateX(${q*shift}px) scale(${1+grow*q})`:'none';c.style.filter=q>.01?`blur(${q*soft}px)`:'none';});
 };
 // Motion blur: a directional Gaussian sized from how far the element moved on screen since the previous frame. Moves
 // slower than `still` pixels a frame stay sharp; `scale` turns screen pixels into the element's own units.
 const filters=new Map();
 const smear=(el,dx,dy,{scale=1,still=2,gain=.3,max=48,extra=''}={})=>{
  if(!filters.has(el))filters.set(el,filters.size);const k=filters.get(el);
  const sx=Math.min(max,Math.max(0,Math.abs(dx)-still)*gain),sy=Math.min(max,Math.max(0,Math.abs(dy)-still)*gain),on=sx>.6||sy>.6;
  if(on)document.getElementById('mb'+k).firstElementChild.setAttribute('stdDeviation',`${(sx/scale).toFixed(2)} ${(sy/scale).toFixed(2)}`);
  el.style.filter=[on?`url(#mb${k})`:'',extra].filter(Boolean).join(' ')||'none';
 };
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
 const travel=t=>{t=Math.min(t,huntStart);return t*20+1600*smooth((t-M.rush)/1.4);};
 const scrollAt=(i,t)=>{const free=(travel(t)+i*137)%LOOP,h=huntFor(i);if(!h)return free;
  const move=h[0]-1,target=(rowIndex(i,h[2])-SLOT)*ROW;return t<move?free:lerp((travel(move)+i*137)%LOOP,target,ease((t-move)/.6));};
 const rowCenter=i=>[L.windows[i][0]+250,L.windows[i][1]+72+SLOT*ROW+23];
 // Where each card is: dropped in, then swept to the centre at the cut to Wizard.
 const portalAt=(i,t)=>{const gather=ease((t-M.gather)/.8),p=ease((t-M.portals[i])/.55),focus=clamp(1-Math.abs(t-portalCues[i]-.7)/1.1);
  return {x:(960-(L.windows[i][0]+232))*gather,y:(500-(L.windows[i][1]+280))*gather+(1-p)*160,s:(1+focus*.025)*(1-gather*.6)*(.94+.06*p),p,gather,focus};};
 const fileAt=(i,t)=>{const p=ease((t-M.files[i%4]-(i>3?.25:0)+.15)/.5),slot=L.files[i],gather=ease((t-M.gather)/.8);
  return {x:(960-(slot[0]+165))*gather,y:(500-(slot[1]+37))*gather-(1-p)*130,r:slot[2]*(1-gather),s:1-gather*.6,p,gather};};
 // The question types in large over the blurred clutter, then flies up into its search bar as the hunt begins.
 const dockAt=t=>whip((t-M.dock)/.5);
 function mess(t){
  // While the question is up, everything behind it drops out of focus under a light veil.
  const end=at('sMess').end,gather=ease((t-M.gather)/.8),veil=ease((t-M.question+.2)/.35)*(1-ease((t-M.dock)/.45)),soft=s=>veil>.01?`blur(${(veil*4/s).toFixed(2)}px)`:'';
  $$('[data-portal]').forEach((el,i)=>{
   const a=portalAt(i,t),b=portalAt(i,t-DT);
   el.style.opacity=a.p*(1-a.gather);el.style.transform=`translate(${a.x}px,${a.y}px) scale(${a.s})`;smear(el,a.x-b.x,a.y-b.y,{scale:a.s,extra:soft(a.s)});
   el.style.zIndex=a.focus>.05?10:i;el.style.boxShadow=a.focus>.05?`0 0 0 ${5*a.focus}px ${['#1d4f91','#6551a8','#0e7490','#9a3412'][i]}33,0 24px 50px #18233b26`:'';
   const offset=scrollAt(i,t),list=el.querySelector('.report-scroll');let d=offset-scrollAt(i,t-DT);if(Math.abs(d)>LOOP/2)d-=Math.sign(d)*LOOP;
   list.style.transform=`translateY(${-offset}px)`;smear(list,0,d*a.s,{scale:a.s,still:3});
   const sought=huntFor(i)?ease((t-huntFor(i)[0]+1)/.6):0;el.querySelector('.list-thumb').style.transform=`translateY(${8+clamp(travel(t)/1900)*110+sought*24}px)`;
   const rows=[...el.querySelectorAll('.report-row')],h=huntFor(i);rows.forEach(r=>r.classList.remove('hit'));
   if(h&&t>=h[0])rows[rowIndex(i,h[2])].classList.add('hit');
  });
  $$('[data-file]').forEach((el,i)=>{
   const a=fileAt(i,t),b=fileAt(i,t-DT);
   el.style.opacity=a.p*(1-a.gather);el.style.transform=`translate(${a.x}px,${a.y}px) rotate(${a.r}deg) scale(${a.s})`;smear(el,a.x-b.x,a.y-b.y,{scale:a.s,extra:soft(a.s)});
   el.classList.toggle('hit',hunt.some(([when,w,target])=>w==='file'&&target===i&&t>=when));
  });
  const d=dockAt(t);$('.hunt-veil').style.opacity=veil*.55;
  const st=$('.hunt-statement'),stAt=x=>({y:-407*dockAt(x),s:1-.684*dockAt(x)}),sa=stAt(t),sb=stAt(t-DT);
  kinetic(st,t,M.question,{gap:.026});st.style.opacity=1-clamp((d-.65)/.35);st.style.visibility=t>=M.question-.1&&d<1?'inherit':'hidden';
  st.style.transform=`translateY(${sa.y}px) scale(${sa.s})`;smear(st,0,sa.y-sb.y,{scale:sa.s});
  const q=clamp((d-.55)/.45)*(1-gather);$('.hunt-question').style.opacity=q;$('.hunt-question').style.transform='translate(-50%,0)';
  const clock=ease((t-cue.look)/.4)*(1-gather);$('.hunt-clock').style.opacity=clock;
  const minutes=Math.round(240*clamp((t-cue.look)/(seg[2].end-cue.look)));$('.hunt-clock span').textContent=minutes<60?`${minutes} min`:`${Math.floor(minutes/60)} h ${String(minutes%60).padStart(2,'0')} min`;
  // Arrive, dwell, click, then move on.
  const path=[[huntStart,960,90],...hunt.flatMap(([when,w,target])=>{const [x,y]=w==='file'?[L.files[target][0]+60,L.files[target][1]+37]:rowCenter(w);return [[when-.3,x,y],[when+.2,x,y]];})];
  pointer($('.hunt-pointer'),t,path,hunt.map(h=>h[0]),huntStart,end-.8);
 }

 // 2. Everything comes together in Wizard.
 const scattered=[...L.windows.map(([x,y])=>[x+232,y+280]),...L.files.slice(0,4).map(([x,y])=>[x+165,y+37])];
 function join(t){
  const start=at('sJoin').start,nodeAt=(i,x)=>{const g=ease((x-M.join)/1.3),[sx,sy]=scattered[i],[rx,ry]=L.ring[i];return {x:lerp(sx,rx,g),y:lerp(sy,ry,g),s:lerp(.82,1,g)};};
  $$('[data-node]').forEach((el,i)=>{
   const a=nodeAt(i,t),b=nodeAt(i,t-DT);
   el.style.opacity=ease((t-start-i*.04)/.4);el.style.transform=`translate(${a.x-125}px,${a.y-42}px) scale(${a.s})`;smear(el,a.x-b.x,a.y-b.y,{scale:a.s});
  });
  const lines=ease((t-cue.together-.6)/.6);$$('[data-join-line]').forEach(el=>el.style.opacity=lines);
  $('.join-flow').style.strokeDashoffset=-t*70;
  const hub=ease((t-cue.meetWizard+.1)/.5),pulse=t>=cue.lives?Math.sin(clamp((t-cue.lives)/.8)*Math.PI)*.08:0;
  $('.join-hub').style.opacity=hub;$('.join-hub').style.transform=`scale(${.6+.4*hub+pulse})`;
  kinetic($('.join-name'),t,M.name,{gap:.05,dur:.45,grow:.9,shift:20,soft:8});
  const ai=ease((t-cue.localAI+.1)/.45);$('.join-ai').style.opacity=ai;$('.join-ai').style.transform=`translate(-50%,${(1-ai)*12}px)`;
 }

 // 3. The real Wizard app. The camera keeps a page point (fx, fy) at the frame centre at zoom z; near z = 1 the window
 // eases back to the centre, and once it is wider than the frame it never shows its edge.
 const clips=config.screen.clips,ids=Object.keys(clips);
 const camera=(keys,t)=>{let i=0;while(i<keys.length-1&&t>=keys[i+1][0])i++;const a=keys[i],b=keys[Math.min(i+1,keys.length-1)];
  const p=b===a||t<=a[0]?0:smooth(clamp((t-a[0])/(b[0]-a[0])));return [lerp(a[1],b[1],p),lerp(a[2],b[2],p),lerp(a[3],b[3],p)];};
 const place=(fx,fy,z)=>{const w=clamp((z-1)/.3);let x=lerp(960-800*z,960-fx*z,w),y=lerp(540-450*z,540-fy*z,w);
  if(1600*z>=1920)x=clamp(x,1920-1600*z,0);if(900*z>=1080)y=clamp(y,1080-900*z,0);return [x,y];};
 // Capture time and speed at a film time, from a timelapse's anchors (its shot's film/capture breakpoints).
 const speedAt=(c,t)=>{const a=c.anchors;for(let i=0;i<a.length-1;i++)if(t>=a[i][0]&&t<a[i+1][0])return (a[i+1][1]-a[i][1])/(a[i+1][0]-a[i][0]);return 1;};
 const sourceAt=(c,t)=>{const a=c.anchors;let i=0;while(i<a.length-2&&t>=a[i+1][0])i++;const [f0,s0]=a[i],[f1,s1]=a[i+1];return s0+(s1-s0)*clamp((t-f0)/Math.max(.001,f1-f0));};
 const lapses=ids.flatMap(id=>clips[id].timelapses),last=ids[ids.length-1];
 // Hand-offs between windows: the first grows out of the hub logo; the second question whips in from the right, and
 // its check runs in that same window; at the end the app pulls away.
 const slide=(t,from)=>whip((t-from)/.6);
 const winAt=(id,t)=>{const c=clips[id],grow=ease((t-at('sApp').start)/.75),[fx,fy,z]=camera(c.camera,t);let [x,y]=place(fx,fy,z),k=1,o=1;
  if(id==='ask'){k=lerp(.18,1,grow);x=960+(x-960)*k;y=540+(y-540)*k+(1-grow)*-40;o=clamp(grow*1.6);}
  if(id===last){const out=ease((t-M.away)/.6);k=1-out*.2;x=960+(x-960)*k;y=540+(y-540)*k-out*160;o=1-out;}
  return {x:x+{ask:-2050*slide(t,M.slide),stock:2050*(1-slide(t,M.slide))}[id],y,s:z*k,o};};
 function screen(t){
  const start=at('sApp').start,end=at('sApp').end;
  const grow=ease((t-start)/.75);
  $('.hub-echo').style.opacity=t<start+.6?1-grow:0;$('.hub-echo').style.transform=`scale(${1+grow*2.2})`;
  const dim=ease((t-cue.safe+.15)/.45)*(1-ease((t-end+.5)/.4));
  ids.forEach(id=>{
   const c=clips[id],el=$(`[data-clip="${id}"]`),on=t>=c.start&&t<c.end;el.style.visibility=on?'inherit':'hidden';if(!on)return;
   const a=winAt(id,t),b=winAt(id,t-DT),zoom=Math.abs(a.s-b.s);
   el.style.opacity=a.o;el.style.transform=`translate(${a.x}px,${a.y}px) scale(${a.s})`;
   // Pans and zooms blur along their motion; behind the security card the app also drops out of focus.
   smear(el,Math.max(Math.abs(a.x-b.x),zoom*800),Math.max(Math.abs(a.y-b.y),zoom*450),{scale:a.s,still:6,gain:.28,max:40,extra:dim>.01?`blur(${(dim*5/a.s).toFixed(2)}px)`:''});
  });
  // Timelapse, only while Wizard works: the pill and a blue frame edge stay up for exactly that stretch. The pill shows
  // how many times faster than real time it plays and the real time the run has taken, counting at that speed.
  const tl=lapses.find(l=>t>=l.from&&t<l.to+.3);
  const on=tl?ease((t-tl.from)/.25)*(1-ease((t-tl.to)/.3)):0,pill=$('.tl-pill');
  pill.style.opacity=on;$('.tl-edge').style.opacity=on;
  if(tl){const at=Math.min(t,tl.to-.001),real=Math.max(0,sourceAt(tl,at)-tl.clockFrom);
   $('.tl-clock').textContent=`${Math.floor(real/60)}:${String(Math.floor(real%60)).padStart(2,'0')}`;
   $('.tl-speed').textContent=`${Math.round(speedAt(tl,at))}×`;
   pill.style.transform=`translate(-50%,${(1-ease((t-tl.from)/.3))*-16}px)`;
   pill.querySelector('.lu').style.opacity=.55+.45*Math.abs(Math.sin((t-tl.from)*5));}
  reveal($('.real-tag'),ease((t-start-.9)/.5)*(1-ease((t-end+.6)/.4)),-8);
  // Safe: the app steps back and the security card opens on "safe".
  $('.screen-dim').style.opacity=dim*.82;
  const card=$('.safe-card'),cp=ease((t-cue.safe)/.45)*(1-ease((t-end+.5)/.4));kinetic(card.querySelector('b>span'),t,M.safe,{gap:.03});card.style.opacity=cp;card.style.transform=`translate(-50%,calc(-50% + ${(1-cp)*18}px)) scale(${.97+.03*cp})`;
  $$('.safe-card>div').forEach((el,i)=>reveal(el,ease((t-[cue.authorized,cue.authorized+.7,cue.allowed+.5][i]+.1)/.35),8));
 }

 // 4. End card: the statement, then Wizard's logo, the tagline, every source.
 function endCard(t){
  $$('.end-statement p').forEach(p=>kinetic(p,t,M.say[p.dataset.say],{gap:.032,dur:.45,grow:.8,shift:22,soft:9}));
  const away=x=>-230*ease((x-M.logo)/.45),st=$('.end-statement');
  st.style.opacity=1-ease((t-M.logo)/.4);st.style.transform=`translateY(${away(t)}px)`;smear(st,0,away(t)-away(t-DT));
  const logo=ease((t-M.logo-.1)/.5),pulse=t>=cue.endWizard?Math.sin(clamp((t-cue.endWizard)/.6)*Math.PI)*.025:0,lockup=$('.end-lockup');
  lockup.style.opacity=clamp(logo*1.6);lockup.style.transform=`scale(${1.35-.35*logo+pulse})`;lockup.style.filter=logo<1?`blur(${((1-logo)*12).toFixed(2)}px)`:'none';
  kinetic($('.end-tagline'),t,M.tagline,{gap:.03});
  $$('[data-end-chip]').forEach((el,i)=>reveal(el,ease((t-M.tagline-.6-i*.06)/.4),12));
 }

 function draw(t){
  scenes.forEach(s=>{const active=t>=s.start&&t<s.end;s.el.style.visibility=active?'visible':'hidden';s.el.style.opacity=active?'1':'0';});
  mess(t);join(t);screen(t);endCard(t);
 }
 window.Film={draw,seek:draw};window.__timelines=window.__timelines||{};const clock={t:0},timeline=gsap.timeline({paused:true});timeline.to(clock,{t:config.duration,duration:config.duration,ease:'none',onUpdate:()=>draw(clock.t)},0);window.__timelines['metronome-your-data-connected']=timeline;window.addEventListener('hf-seek',e=>draw(e.detail.time));draw(0);
})();
