(() => {
 const config=window.FILM_CONFIG,clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x)),ease=x=>1-Math.pow(1-clamp(x),3);
 const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],show=(el,on)=>el.style.visibility=on?'inherit':'hidden';
 const scenes=config.scenes.map(s=>({...s,el:document.getElementById(s.id)}));
 function cursorAt(el,t,points){let from=points[0],to=points.at(-1);for(let i=1;i<points.length;i++){if(t<points[i][0]){from=points[i-1];to=points[i];break;}from=points[i];}const p=ease((t-from[0])/Math.max(.001,to[0]-from[0]));el.style.transform=`translate(${from[1]+(to[1]-from[1])*p}px,${from[2]+(to[2]-from[2])*p}px)`;}
 const script='import requests\n\nsession = requests.Session()\nresponse = session.get(\n    "https://asap-portal.com"\n)\n\ndef parse_report(response):\n    rows = []\n    # Map every column';
 const laneBots=$$('[data-lane]').map(lane=>{const bot=lane.querySelector('.pipeline-agent');bot.style.setProperty('--agent-color',lane.style.getPropertyValue('--agent-color'));lane.parentElement.appendChild(bot);return bot;});
 function draw(realTime){
 const t=realTime<37?realTime:realTime<57?realTime+2:realTime+13;
 scenes.forEach(s=>{const active=realTime>=s.start&&realTime<s.end;s.el.style.visibility=active?'visible':'hidden';s.el.style.opacity=active?'1':'0';});
 $('.intro-asap').style.transform=`translateY(${(1-ease(t/.45))*25}px)`;
 $('.intro-gscm').style.opacity=ease((t-.8)/.6);$('.intro-gscm').style.transform=`translateX(${(1-ease((t-.8)/.6))*55}px)`;
 $$('.source-item').forEach((el,i)=>{const p=ease((t-6-i*.07)/.4);el.style.opacity=p;el.style.transform=`translateX(${(1-p)*-24}px)`;});
 $$('.dataset').forEach((el,i)=>{const p=ease((t-8-i*.3)/.5);el.style.opacity=p;el.style.transform=`translateX(${(1-p)*25}px)`;});
 $('.flow-dashes').style.strokeDashoffset=-(t-6)*33;
 window.FilmBuilder.draw(41.4+(t-16)*1.18);
 const code=script.slice(0,Math.floor(clamp((t-16)/25)*script.length));$('#slow-code').textContent=code;const codeLines=code.split('\n');
 $('.code-caret').style.left=(62+codeLines.at(-1).length*12.5)+'px';$('.code-caret').style.top=(37+(codeLines.length-1)*42)+'px';$('.code-caret').style.opacity=Math.floor(t*3)%2?1:0;
 // Different phases and report lanes keep every stage under continuous inspection.
 const xStops=[74,650.3,1254];
 $$('[data-lane]').forEach((lane,i)=>{const local=Math.max(0,t-39)+i*.71,routes=[[0,i],[1,i],[2,i],[1,(i+1)%6],[0,(i+1)%6],[2,(i+2)%6]],step=Math.floor(local/2.1),phase=local%2.1,from=routes[step%6],to=routes[(step+1)%6],p=ease((phase-.48)/1.62),bot=laneBots[i];
 bot.style.transform=`translate(${xStops[from[0]]+(xStops[to[0]]-xStops[from[0]])*p}px,${(from[1]+(to[1]-from[1])*p)*68}px)`;
 bot.classList.toggle('scanning',phase<.48);bot.querySelector('.agent-character').style.transform=`translateY(${Math.sin(local*7)*2}px)`;bot.querySelector('.agent-scan').style.opacity=phase<.48?'.65':'0';
 const complete=t>=44+i*.8;lane.classList.toggle('complete',complete);lane.classList.toggle('flagged',complete&&i<5);lane.querySelector('.lane-result').textContent=complete?(i<5?'!':'✓'):'';lane.querySelector('.lane-progress').style.width=(38+Math.sin(local*.7)*35)+'%';});
 const findings=clamp(Math.floor((t-44)/.8)+1,0,5);$('.findings-counter').textContent=findings?`${findings} finding${findings===1?'':'s'}`:'Scanning';$('.findings-counter').classList.toggle('has-findings',findings>0);$('.agent-launch').style.opacity=0;
 $$('[data-issue]').forEach((el,i)=>{show(el,t>=51.1+i*.2);el.classList.toggle('selected',i===1&&t>=54);});
 const expand=ease((t-54)/.5);$('.issue-list').style.width=(100-expand*48)+'%';$('.issue-detail').style.width=(expand*48)+'%';$('.issue-detail').style.opacity=expand;show($('.issue-detail'),t>=54);$$('[data-issue]>span:nth-last-child(2)').forEach(el=>show(el,t<54));
 cursorAt($('.issue-cursor'),t,[[52.5,1400,413],[53.8,460,119],[54.2,460,119],[55,720,360]]);show($('.issue-cursor'),t>=52.5&&t<55);const click=clamp((t-53.9)/.4);$('.issue-cursor>i').style.opacity=t>=53.9&&t<54.3?1-click:0;$('.issue-cursor>i').style.transform=`scale(${.5+click*2})`;
 $('.access-web').style.opacity=ease((t-70)/.45);$('.access-ai').style.opacity=ease((t-76.5)/.5);$$('.ai-choice-logos>div').forEach((el,i)=>el.style.transform=`translateY(${(1-ease((t-76.8-i*.16)/.4))*22}px)`);$('.prompt-flow').style.opacity=ease((t-79.5)/.5);
 show($('.chat-comparison'),t<100);show($('.outcome-table'),t>=100);const comparison=t-83;
 $$('[data-retry]').forEach((el,i)=>show(el,comparison>=[.4,2.3,4.6,6.4,7.7][i]));const right=ease((comparison-8.2)/.5);$('.connection-chip').style.opacity=right;$('.mcp-run').style.opacity=right;
 $$('[data-step]').forEach((el,i)=>{const p=ease((comparison-9-i*.8)/.45);el.style.opacity=p;el.style.transform=`translateY(${(1-p)*15}px)`;});const done=ease((comparison-13)/.5);$('.chat-result').style.opacity=done;$('.chat-result').style.transform=`scale(${.97+done*.03})`;
 $('.mcp-run').style.boxShadow=`0 0 ${18+Math.sin(comparison*3)*7}px rgba(45,155,112,${comparison>8.2&&comparison<14.5?.18:0})`;
 // Word-aligned paired reveals use real film time, so seek/backward playback is deterministic.
 $$('[data-compare-row]').forEach((el,i)=>{const p=ease((realTime-config.closingCues.cues[i].start)/config.closingCues.revealSeconds);el.style.opacity=p;el.style.transform=`translateY(${(1-p)*12}px)`;});
 }
 window.Film={draw,seek:draw};window.__timelines=window.__timelines||{};const clock={t:0},timeline=gsap.timeline({paused:true});timeline.to(clock,{t:config.duration,duration:config.duration,ease:'none',onUpdate:()=>draw(clock.t)},0);window.__timelines['metronome-your-data-connected']=timeline;window.addEventListener('hf-seek',e=>draw(e.detail.time));draw(0);
})();
