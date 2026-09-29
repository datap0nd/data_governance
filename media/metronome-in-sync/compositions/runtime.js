(() => {
  const config = window.FILM_CONFIG;
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const ease = x => 1 - Math.pow(1 - clamp(x), 3);
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const show = (el, visible) => { el.style.visibility = visible ? 'inherit' : 'hidden'; };
  const scenes = config.scenes.map(s => ({...s, el: document.getElementById(s.id)}));
  const fields = $$('[data-field]');
  const values = $$('[data-value]').map(el => ({el, value: el.textContent}));
  const brandEls = $$('[data-brand]');
  const move = (el, x, y) => { el.style.transform = `translate(${x}px,${y}px)`; };
  function cursorAt(el, t, points) {
    let from = points[0], to = points[points.length - 1];
    for (let i = 1; i < points.length; i++) {
      if (t < points[i][0]) { from = points[i-1]; to = points[i]; break; }
      from = points[i];
    }
    const p = ease((t-from[0])/Math.max(.001,to[0]-from[0]));
    move(el,from[1]+(to[1]-from[1])*p,from[2]+(to[2]-from[2])*p);
  }
  function draw(t) {
    for (const scene of scenes) {
      const active = t >= scene.start && t < scene.end;
      scene.el.style.visibility = active ? 'visible' : 'hidden';
      scene.el.style.opacity = active ? '1' : '0';
    }
    $('.intro-asap').style.transform = `translateY(${(1-ease(t/.7))*25}px)`;
    $('.intro-gscm').style.opacity = String(ease((t-2.6)/.8));
    $('.intro-gscm').style.transform = `translate(${(1-ease((t-2.6)/.8))*70}px,0)`;
    $$('.source-item').forEach((el,i) => {const p=ease((t-10-i*.12)/.5);el.style.opacity=p;el.style.transform=`translateX(${(1-p)*-24}px)`;});
    $$('.dataset').forEach((el,i) => {const p=ease((t-15-i*.5)/.65);el.style.opacity=p;el.style.transform=`translateX(${(1-p)*25}px)`;});
    $('.flow-dashes').style.strokeDashoffset=String(-(t-10)*24);
    $$('.ai-tool-row>div').forEach((el,i)=>{const p=ease((t-27.4-i*.32)/.6);el.style.opacity=p;el.style.transform=`translateY(${(1-p)*24}px)`;});

    show($('.no-code-intro'),t<41.4);
    show($('.builder-browser'),t>=41.4);
    $('.builder-browser').style.opacity=ease((t-41.4)/.45);
    $('.code-sheet').style.transform=`translateX(${-ease((t-40.7)/.7)*60}px)`;
    const fieldTimes=[43,46,49,52];
    fields.forEach((el,i)=>{
      const entered=t>=fieldTimes[i];
      el.classList.toggle('selected',entered&&t<fieldTimes[i]+2.4);
      values[i].el.textContent=entered?values[i].value.slice(0,Math.ceil(clamp((t-fieldTimes[i])/.9)*values[i].value.length)):'—';
      show(el.querySelector('.i'),t>=fieldTimes[i]+1.1);
    });
    $$('.pipeline-node').forEach((el,i)=>{const p=ease((t-57.5-i*.35)/.5);el.style.opacity=p;el.style.transform=`translateY(${(1-p)*18}px)`;});
    $$('.node-link').forEach((el,i)=>el.style.opacity=ease((t-58-i*.35)/.5));
    $('.pipeline-status').style.opacity=ease((t-59.1)/.5);
    $('.create-flow').textContent=t>=58.2?'Flow created ✓':'Create Flow';
    cursorAt($('.flow-cursor'),t,[[41.4,1530,530],[42.9,210,165],[45.7,210,275],[48.7,210,382],[51.7,210,490],[56.8,1480,542],[58.3,1480,542],[59.4,1580,580]]);
    const click=ease((t-57.4)/.6);$('.flow-cursor>i').style.opacity=t>=57.4&&t<58.1?1-click:0;$('.flow-cursor>i').style.transform=`scale(${.5+click*2})`;
    show($('.flow-cursor'),t>=41.4&&t<60);

    $('.scan-beam').style.transform=`translateX(${((Math.max(0,t-64)*65)%660)}px)`;
    $$('[data-audit]').forEach((el,i)=>{el.classList.toggle('checked',t>=69+i*2);show(el.querySelector('.i'),t>=69+i*2);});

    $$('[data-issue]').forEach((el,i)=>{show(el,t>=78.4+i*.45);el.classList.toggle('selected',i===1&&t>=84);});
    const expand=ease((t-84)/.8);
    $('.issue-list').style.width=(100-expand*48)+'%';
    $('.issue-detail').style.width=(expand*48)+'%';
    $('.issue-detail').style.opacity=expand;
    show($('.issue-detail'),t>=84);
    $$('[data-issue]>span:nth-last-child(2)').forEach(el=>show(el,t<84));
    cursorAt($('.issue-cursor'),t,[[81.8,1400,413],[83.7,460,119],[84.6,460,119],[86,720,360]]);
    show($('.issue-cursor'),t>=81.8&&t<86);
    const issueClick=clamp((t-83.8)/.5);$('.issue-cursor>i').style.opacity=t>=83.8&&t<84.4?1-issueClick:0;$('.issue-cursor>i').style.transform=`scale(${.5+issueClick*2})`;

    $('.trend-line').style.strokeDasharray='1600';$('.trend-line').style.strokeDashoffset=String((1-ease((t-95)/2))*1600);
    $('.api-bridge').style.opacity=ease((t-100.8)/.8);
    $('.api-path i').style.left=(clamp(((t-101)%3)/3)*218)+'px';
    $('.server-line i').style.left=(clamp(((t-110)%2)/2)*192)+'px';
    $('.access-url').style.opacity=ease((t-111.2)/.7);

    const aiIndex=clamp(Math.floor((t-122)/6.6),0,2);
    brandEls.forEach(el=>show(el,Number(el.dataset.brand)===aiIndex));
    const local=Math.max(0,t-122-aiIndex*6.6);
    const boost=ease((local-.8)/1.2);
    $('.ai-output').style.transform=`scale(${.82+boost*.18})`;
    $('.boost-ring').style.opacity=boost;
    $$('.power-orbit,.boost-spark').forEach((el,i)=>{const p=ease((local-1.4-i*.18)/.6);el.style.opacity=p;el.style.transform=`scale(${.65+p*.35}) translateY(${Math.sin(local*1.5+i)*3}px)`;});
  }
  window.Film={draw,seek:draw};
  window.__timelines=window.__timelines||{};
  const clock={t:0};
  const timeline=gsap.timeline({paused:true});
  timeline.to(clock,{t:config.duration,duration:config.duration,ease:'none',onUpdate:()=>draw(clock.t)},0);
  window.__timelines['metronome-your-data-connected']=timeline;
  window.addEventListener('hf-seek',e=>draw(e.detail.time));
  draw(0);
})();
