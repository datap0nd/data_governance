(() => {
 const config=window.FILM_CONFIG,cue=config.cues,clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x)),ease=x=>1-Math.pow(1-clamp(x),3);
 const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],show=(el,on)=>el.style.visibility=on?'inherit':'hidden';
 const scenes=config.scenes.map(s=>({...s,el:document.getElementById(s.id)}));
 const reveal=(el,p,dy=14)=>{el.style.opacity=p;el.style.transform=`translateY(${(1-p)*dy}px)`;};
 const script='import requests\n\nsession = requests.Session()\nresponse = session.get(\n    "https://asap-portal.com"\n)\n\ndef parse_report(response):\n    rows = []\n    # Map every column';
 const question=$('.wz-user').textContent;
 const wz=$('.wz'),thread=$('.wz-thread'),steps=$('.wz-steps'),stage=$('.wz-stage'),composerWrap=$('.wz-composer-wrap'),mainText=$$('.wz-main *');
 // Wizard pointer: keyframes name live elements, so targets follow the layout at every seek.
 const centerOf=(sel,dx=0,dy=0)=>()=>{const r=$(sel).getBoundingClientRect(),o=wz.getBoundingClientRect();return [r.left-o.left+r.width/2+dx,r.top-o.top+r.height/2+dy];};
 const pointerPath=[[72.5,centerOf('.wz-composer',120,40)],[73.3,centerOf('.wz-send')],[73.75,centerOf('.wz-send')],[74.3,centerOf('.wz-send',-60,90)],
  [cue.source-.2,centerOf('.wz-prepared',40,40)],[cue.checkData-.2,centerOf('.wz-check-btn')],[cue.checkData+.15,centerOf('.wz-check-btn')],
  [cue.evidence-.25,centerOf('.wz-sources-btn')],[cue.evidence+.1,centerOf('.wz-sources-btn')]];
 const pointerClicks=[73.55,cue.checkData-.05,cue.evidence-.08];
 function pointer(t){
  const el=$('.wz-cursor'),visible=(t>=72.5&&t<74.3)||(t>=cue.source-.2&&t<cue.evidence+.35);
  el.style.visibility=visible?'inherit':'hidden';if(!visible)return;
  let from=pointerPath[0],to=pointerPath[0];
  for(let i=1;i<pointerPath.length;i++){to=pointerPath[i];if(t<=to[0])break;from=to;}
  const a=from[1](),b=to[1](),p=ease((t-from[0])/Math.max(.001,to[0]-from[0]));
  el.style.transform=`translate(${a[0]+(b[0]-a[0])*p-4.3}px,${a[1]+(b[1]-a[1])*p-3.25}px)`;
  const last=pointerClicks.filter(at=>at<=t).at(-1)??-100,age=t-last;
  el.querySelector('.pointer').style.transform=`scale(${age>=0&&age<.14?.87:1})`;
  const ring=el.querySelector('i');ring.style.opacity=age>=0&&age<.48?1-age/.48:0;ring.style.transform=`scale(${.4+clamp(age/.48)*1.3})`;
 }
 const contentBottom=el=>el.getBoundingClientRect().bottom-thread.getBoundingClientRect().top;
 function wizard(t){
  const browser=ease((t-57)/.6);$('.wizard-browser').style.opacity=browser;$('.wizard-browser').style.transform=`translateY(${(1-browser)*40}px)`;
  // Empty state, then the question is typed and sent.
  const sent=t>=73.6,gone=ease((t-73.6)/.3);
  $('.wz-empty').style.opacity=sent?1-gone:1;
  $$('.wz-empty>p,.wz-empty>h1').forEach((el,i)=>reveal(el,ease((t-57.35-i*.18)/.5)));
  $$('[data-suggestion]').forEach((el,i)=>reveal(el,ease((t-cue.plainLanguage-i*.2)/.5),20));
  $('[data-suggestion="0"]').classList.toggle('picked',t>=cue.askQuestion&&!sent);
  const typeStart=cue.askQuestion+.15,typed=sent?'':question.slice(0,Math.floor(clamp((t-typeStart)/4.2)*question.length));
  $('.wz-typed').textContent=typed;$('.wz-placeholder').style.display=typed?'none':'';
  const focused=t>=cue.askQuestion-.6&&!sent;$('.wz-composer').classList.toggle('focused',focused);
  $('.wz-caret').style.opacity=focused&&Math.floor(t*2.5)%2===0?1:0;
  $('.wz-send').classList.toggle('ready',typed.length===question.length);$('.wz-send').classList.toggle('pressed',t>=73.55&&t<73.7);
  $('.wz-title-new').style.display=t>=73.8?'none':'inline';$('.wz-title-q').style.display=t>=73.8?'inline':'none';
  $$('.wz-today').forEach(el=>el.style.display=t>=74?'':'none');
  reveal($('.wz-user'),ease((t-73.8)/.4));reveal($('.wz-run'),ease((t-74)/.45),24);
  // Steps stream in on the sources the narration names.
  const stepAt={note:74.6,0:cue.readSpend,1:cue.readSellOut,2:cue.readShare,3:cue.readShare+1.1,4:cue.readShare+1.9},done=cue.readShare+2.3;
  let count=0;
  $$('[data-step]').forEach(el=>{const at=stepAt[el.dataset.step],on=t>=at;el.style.display=on?'grid':'none';reveal(el,ease((t-at)/.35),10);if(on&&el.dataset.step!=='note')count++;});
  $('.wz-step-count').textContent=`${count} step${count===1?'':'s'}`;
  $('.wz-deciding').style.display=t>=74.3&&t<done?'flex':'none';$('.wz-deciding i').style.opacity=.35+.65*Math.abs(Math.sin(t*3.2));
  const elapsed=Math.max(0,Math.min(t,done)-74);$('.wz-elapsed').textContent=t<74?'':`0:${String(Math.floor(elapsed)).padStart(2,'0')}`;
  const callout=$('.fed-callout'),calloutP=ease((t-cue.loadedByMetronome)/.35)*(1-ease((t-cue.readShare+.35)/.3));
  callout.style.opacity=calloutP;
  if(calloutP>0){const anchor=$('[data-step="1"] code').getBoundingClientRect(),sceneBox=$('#sWiz').getBoundingClientRect();callout.style.transform=`translate(${anchor.left-sceneBox.left+120}px,${anchor.bottom-sceneBox.top+18+(1-calloutP)*10}px)`;}
  // The timeline folds away and the answer, chart and actions appear.
  const fold=ease((t-cue.answer+.4)/.6);
  steps.style.maxHeight='none';const stepsHeight=steps.scrollHeight;steps.style.maxHeight=`${stepsHeight*(1-fold)}px`;steps.style.opacity=1-fold;
  steps.style.borderTopColor=fold>=1?'transparent':'#e0e4eb';steps.style.paddingTop=steps.style.paddingBottom=fold>=1?'0':'';
  $('.wz-chevron').style.transform=`rotate(${180*(1-fold)}deg)`;
  reveal($('.wz-answer>p'),ease((t-cue.answer)/.45));reveal($('.wz-chart'),ease((t-cue.answer-.4)/.5));
  $$('[data-bar]').forEach((el,i)=>{const p=ease((t-cue.egypt-i*.2)/.7);el.querySelector('.wz-track').style.setProperty('--p',p);el.querySelector('.wz-track>span').style.opacity=p;});
  reveal($('.wz-actions'),ease((t-cue.egypt-.7)/.4),8);
  // Evidence links light up, Check my data recomputes, then the evidence drawer shows the query.
  $$('.wz-prepared .wz-eid').forEach((el,i)=>el.classList.toggle('lit',t>=cue.source+i*.12&&t<cue.checkData));
  $('.wz-check-btn').classList.toggle('hover',t>=cue.checkData-.35&&t<cue.checkData-.05);$('.wz-check-btn').classList.toggle('pressed',t>=cue.checkData-.05&&t<cue.checkData+.15);
  $('.wz-sources-btn').classList.toggle('hover',t>=cue.evidence-.4&&t<cue.evidence-.08);$('.wz-sources-btn').classList.toggle('pressed',t>=cue.evidence-.08&&t<cue.evidence+.1);
  reveal($('.wz-check'),ease((t-cue.checkData-.1)/.4),10);
  $$('[data-match]').forEach((el,i)=>el.style.opacity=ease((t-cue.checkData-.3-i*.13)/.25));
  const checked=ease((t-cue.checkData-.8)/.3);$('.wz-checked').style.opacity=checked;$('.wz-unchecked').style.opacity=1-checked;
  // Scroll like the chat column: keep the newest content above the composer.
  const room=stage.clientHeight-composerWrap.offsetHeight-thread.offsetTop-18,current=steps.getBoundingClientRect().height;
  const answerScroll=Math.max(0,contentBottom($('.wz-actions'))-(fold<1?current:0)-room),checkScroll=Math.max(0,contentBottom($('.wz-check'))-(fold<1?current:0)-room);
  const scrolled=answerScroll*ease((t-cue.answer+.2)/.8)+(checkScroll-answerScroll)*ease((t-cue.checkData-.1)/.7);
  thread.style.transform=`translateY(${-scrolled}px)`;
  const drawer=ease((t-cue.evidence)/.45);$('.wz-drawer').style.transform=`translateX(${(1-drawer)*102}%)`;$('.wz-drawer').style.visibility=drawer>0?'inherit':'hidden';$('.wz-scrim').style.opacity=drawer*.16;
  // Scrolled-away rows and the page under the drawer are covered on purpose; mark them only while that is true.
  const covered=drawer>0||scrolled>0;mainText.forEach(el=>{el.toggleAttribute('data-layout-allow-occlusion',covered);el.toggleAttribute('data-layout-allow-overlap',covered);});
  $('.wz-sql').classList.toggle('lit',t>=cue.evidence+1.1);
  pointer(t);
 }
 function closing(t){
  const l=t-101.5;
  [['.close-asap',101.6],['.close-gscm',101.75],['.close-metronome',cue.closeMetronome],['.close-db',cue.closeMetronome+.9],['.close-wizard',cue.closeWizard],['.close-answer',cue.closeWizard+1.2]].forEach(([sel,at])=>{const p=ease((t-at)/.5),el=$(sel);el.style.opacity=p;el.style.transform=`translateY(${(1-p)*18}px) scale(${.97+.03*p})`;});
  [cue.closeMetronome+.2,cue.closeMetronome+.8,cue.closeWizard+.2,cue.closeWizard+1.1].forEach((at,i)=>$(`[data-close-link="${i}"]`).style.opacity=ease((t-at)/.4));
  $('.close-dashes').style.strokeDashoffset=-l*42;
  // The diagram sits centred, then makes room for the paired lockup.
  const lockup=ease((t-cue.decision+.9)/.8);$('.close-diagram').style.transform=`translateY(${(1-lockup)*90}px)`;
  $('.close-lockup').style.opacity=lockup;$('.close-lockup').style.transform=`translateY(${(1-lockup)*30}px)`;
 }
 function draw(realTime){
  const t=realTime;
  if(t>=9&&t<20)$('.etl-scene').innerHTML=etlSVG(t,cue);
  if(t>=20&&t<31)$('.pipeline-controls').innerHTML=capabilitySVG(t,cue);
  scenes.forEach(s=>{const active=t>=s.start&&t<s.end;s.el.style.visibility=active?'visible':'hidden';s.el.style.opacity=active?'1':'0';});
  $('.intro-asap').style.transform=`translateY(${(1-ease(t/.6))*25}px)`;
  $('.intro-gscm').style.opacity=ease((t-1.1)/.8);$('.intro-gscm').style.transform=`translateX(${(1-ease((t-1.1)/.8))*55}px)`;
  $$('.source-item').forEach((el,i)=>{const p=ease((t-20-i*.08)/.45);el.style.opacity=p;el.style.transform=`translateX(${(1-p)*-24}px)`;});
  $$('.dataset').forEach((el,i)=>{const p=ease((t-22.2-i*.33)/.55);el.style.opacity=p;el.style.transform=`translateX(${(1-p)*25}px)`;});
  $('.flow-dashes').style.strokeDashoffset=-(t-20)*30;
  // Build a Flow: the recorded control events replay faster than authored so the Flow is ready as it is named.
  window.FilmBuilder.draw(41.2+(t-31)*1.4);
  const code=script.slice(0,Math.floor(clamp((t-31)/16)*script.length));$('#slow-code').textContent=code;const codeLines=code.split('\n');
  $('.code-caret').style.left=(62+codeLines.at(-1).length*12.5)+'px';$('.code-caret').style.top=(37+(codeLines.length-1)*42)+'px';$('.code-caret').style.opacity=Math.floor(t*3)%2?1:0;
  // Questions that cross systems: Metronome's tables arrive first, then each question wires to the sources it needs.
  reveal($('.questions-title'),ease((t-47.1)/.5));
  $$('[data-system]').forEach(el=>{const j=+el.dataset.system,p=ease((t-47.3-[.35,.47,.59,0][j])/.5);reveal(el,p,18);});
  $$('[data-question]').forEach((el,i)=>reveal(el,ease((t-cue.questions-i*.35)/.5),22));
  $$('[data-wire]').forEach((el,k)=>el.style.strokeDashoffset=1-ease((t-cue.systems+.2-k*.08)/.7));
  if(t>=56)wizard(t);else wizard(56);
  closing(t);
 }
 window.Film={draw,seek:draw};window.__timelines=window.__timelines||{};const clock={t:0},timeline=gsap.timeline({paused:true});timeline.to(clock,{t:config.duration,duration:config.duration,ease:'none',onUpdate:()=>draw(clock.t)},0);window.__timelines['metronome-your-data-connected']=timeline;window.addEventListener('hf-seek',e=>draw(e.detail.time));draw(0);
})();
