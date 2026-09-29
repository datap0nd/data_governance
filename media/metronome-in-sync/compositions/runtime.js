(()=>{
 const config=window.FILM_CONFIG;const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));const ease=x=>1-Math.pow(1-clamp(x),3);
 const scenes=config.scenes.map(s=>({...s,el:document.getElementById(s.id)}));
 const sourceEls=[...document.querySelectorAll('[data-source]')],datasetEls=[...document.querySelectorAll('[data-dataset]')];
 const stages=[...document.querySelectorAll('[data-stage]')],checks=[...document.querySelectorAll('[data-check]')];
 const details=[['Collect from ASAP','Open the saved report, select the reporting period, download the workbook.'],['Transform the data','Normalize dates, standardize country names, and keep a common product ID.'],['Load the organized database','Write the normalized result to sales.sell_out_daily and retain its run evidence.'],['Schedule and monitor','Run every night at 02:00 Dubai. Review the status, inputs, and output of every step.']];
 function draw(t){
  for(const s of scenes){let local=t-s.start,active=t>=s.start&&t<s.end;s.el.style.visibility=active?'visible':'hidden';s.el.style.opacity=active?1:0;if(active){for(const el of s.el.querySelectorAll('.frame'))el.style.transform=`translateY(${(1-ease(local/0.65))*18}px)`;}}
  sourceEls.forEach((el,i)=>{const a=ease((t-8.35-i*.22)/.65);el.style.opacity=a;el.style.transform=`translateX(${(1-a)*-25}px)`});
  datasetEls.forEach((el,i)=>{const a=ease((t-12.1-i*.58)/.65);el.style.opacity=a;el.style.transform=`translateX(${(1-a)*22}px)`});
  document.querySelector('.flow-dashes').style.strokeDashoffset=String(-(t-8)*24);
  const step=clamp(Math.floor((t-29)/3.15),0,3);stages.forEach((el,i)=>el.classList.toggle('active',i===step));document.querySelector('#step-title').textContent=details[step][0];document.querySelector('#step-description').textContent=details[step][1];
  const n=clamp(Math.floor((t-43.2)/2.4)+1,0,4);checks.forEach((el,i)=>{el.style.opacity=i<n?'1':'.35';el.querySelector('em').textContent=i<n?'Checked':'Queued'});document.querySelector('#audit-count').textContent=String(n);
  const mins=Math.floor(clamp((t-42)/13)*17);document.querySelector('#night-time').textContent='02:'+String(mins).padStart(2,'0');document.querySelector('.night-line i').style.left=(clamp((t-42)/13)*374)+'px';
  document.querySelector('.trend-line').style.strokeDasharray='1100';document.querySelector('.trend-line').style.strokeDashoffset=String((1-ease((t-66.2)/2))*1100);
  const cue=config.narration.find(c=>t>=c.start&&t<c.end);document.querySelector('#korean-subtitle').textContent=cue?cue.ko:'';document.querySelector('.film-progress i').style.width=(clamp(t/84)*100)+'%';
 }
 window.Film={draw,seek:draw};window.__timelines=window.__timelines||{};const clock={t:0};const tl=gsap.timeline({paused:true});tl.to(clock,{t:84,duration:84,ease:'none',onUpdate:()=>draw(clock.t)},0);window.__timelines['metronome-ready-by-morning']=tl;window.addEventListener('hf-seek',e=>draw(e.detail.time));draw(0);
})();
