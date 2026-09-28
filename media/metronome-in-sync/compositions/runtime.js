import * as THREE from 'three';
import data from '../assets/build-data.json';

const T=data.timeline, screens=data.screens?.screens||{};
const sec=b=>b*60/T.bpm, beat=t=>t*T.bpm/60;
const clamp=x=>Math.max(0,Math.min(1,x));
const ease=x=>1-Math.pow(1-clamp(x),5);
const event=id=>T.events.find(x=>x.id===id);
const progress=(id,t)=>{const e=event(id);return clamp((beat(t)-e.startBeat)/Math.max(.001,(e.endBeat??e.startBeat+1)-e.startBeat));};
const since=(id,t)=>beat(t)>=event(id).startBeat;
const active=(e,t)=>beat(t)>=e.startBeat&&beat(t)<(e.endBeat??e.startBeat+1);
const state={};
const opacity=(el,o)=>{if(el)el.style.opacity=String(o)};
const query=(root,s)=>root.querySelector(s);
function rng(seed){return()=>{seed|=0;seed=seed+0x6D2B79F5|0;let a=Math.imul(seed^seed>>>15,1|seed);a=a+Math.imul(a^a>>>7,61|a)^a;return((a^a>>>14)>>>0)/4294967296;};}

function phase(i,t){return t*Math.PI*4+i;}

function make3D(id,canvas){
  const renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(window.devicePixelRatio||1);
  renderer.setSize(T.width,T.height,false);
  renderer.setClearColor(0x07080C,0);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.35;
  const scene=new THREE.Scene();
  scene.fog=new THREE.FogExp2(0x07080C,.025);
  const camera=new THREE.PerspectiveCamera(35,T.width/T.height,.1,140);
  const warm=new THREE.DirectionalLight(0xF4EFE6,5);warm.position.set(-10,14,8);scene.add(warm);
  const rim=new THREE.DirectionalLight(0x5EEAD4,7);rim.position.set(8,6,-10);scene.add(rim);
  scene.add(new THREE.HemisphereLight(0x587a88,0x04050b,1.5));
  const teal=new THREE.PointLight(0x5EEAD4,70,35,2);teal.position.set(-2,5,2);scene.add(teal);
  const bodies=[],arms=[];
  const floor=new THREE.Mesh(new THREE.PlaneGeometry(180,180),new THREE.MeshStandardMaterial({color:0x07080C,metalness:.65,roughness:.38}));
  floor.rotation.x=-Math.PI/2;floor.position.y=-.12;scene.add(floor);
  if(id!=='s5'){
    const shape=new THREE.Shape();shape.moveTo(-.72,0);shape.lineTo(.72,0);shape.lineTo(.416,2.64);shape.lineTo(-.416,2.64);shape.closePath();
    const bodyGeo=new THREE.ExtrudeGeometry(shape,{depth:.54,bevelEnabled:true,bevelSegments:3,steps:1,bevelSize:.045,bevelThickness:.04});
    const bodyMat=new THREE.MeshStandardMaterial({color:0x274847,emissive:0x081518,emissiveIntensity:.55,metalness:.32,roughness:.34});
    const edgeMat=new THREE.LineBasicMaterial({color:0x5EEAD4,transparent:true,opacity:.78});
    const rodMat=new THREE.MeshStandardMaterial({color:0xE5E3DD,metalness:.8,roughness:.21});
    const weightMat=new THREE.MeshStandardMaterial({color:0x5EEAD4,emissive:0x0D7377,emissiveIntensity:.6,metalness:.7,roughness:.25});
    for(let i=0;i<1;i++){
      const g=new THREE.Group();
      const body=new THREE.Mesh(bodyGeo,bodyMat);body.position.z=-.27;g.add(body);
      const edge=new THREE.LineSegments(new THREE.EdgesGeometry(bodyGeo,35),edgeMat);edge.position.z=-.27;g.add(edge);
      const pivot=new THREE.Group();pivot.position.set(0,2.37,.37);
      const rod=new THREE.Mesh(new THREE.CylinderGeometry(.025,.025,1.69,8),rodMat);rod.position.y=-.82;pivot.add(rod);
      const weight=new THREE.Mesh(new THREE.CylinderGeometry(.185,.185,.13,24),weightMat);weight.rotation.x=Math.PI/2;weight.position.set(0,-.99,.065);pivot.add(weight);
      const screw=new THREE.Mesh(new THREE.SphereGeometry(.063,12,12),rodMat);pivot.add(screw);g.add(pivot);
      const foot=new THREE.Mesh(new THREE.BoxGeometry(1.94,.085,.78),rodMat);foot.position.y=-.04;g.add(foot);
      g.position.set(0,0,0);g.rotation.y=.1;
      bodies.push(g);arms.push(pivot);scene.add(g);
    }
  }
  const seeded=rng(T.seed+Number(id.slice(1)));
  const positions=[],speeds=[];
  for(let i=0;i<750;i++){positions.push((seeded()-.5)*55,seeded()*20,(seeded()-.7)*42);speeds.push(seeded());}
  const starsGeo=new THREE.BufferGeometry();starsGeo.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  const stars=new THREE.Points(starsGeo,new THREE.PointsMaterial({color:0x9AC9C4,size:id==='s5'?.06:.026,transparent:true,opacity:.4,sizeAttenuation:true}));scene.add(stars);
  let core;
  if(id==='s5'){
    floor.visible=false;
    core=new THREE.Mesh(new THREE.OctahedronGeometry(1.3,0),new THREE.MeshPhysicalMaterial({color:0x5EEAD4,emissive:0x0d7377,emissiveIntensity:.5,metalness:.4,roughness:.14,transparent:true,opacity:.28,wireframe:true}));
    core.scale.y=2.5;core.position.set(0,3.9,-2);scene.add(core);
  }
  function draw(t){
    if(id==='s1'){
      const u=progress('intro-camera',t),p=u*u*(3-2*u);
      camera.position.set(1.3+1.2*p,2.8+2.4*p,6.8+6*p);camera.lookAt(0,1.4,0);
      canvas.style.opacity=String(ease(progress('intro-fade',t)));
    }else{
      camera.position.set(.3*Math.sin(t*.12),5,18);camera.lookAt(0,3,-1);
      core.rotation.y=t*.13;core.rotation.z=Math.sin(t*.2)*.05;
      const a=starsGeo.attributes.position.array,p=progress('core-converge',t);
      for(let i=0;i<speeds.length;i++){
        const f=(speeds[i]+progress('core-row-stack',t)*2)%1;
        const radius=(1-f)*(10+speeds[i]*13);
        a[i*3]=Math.cos(i*2.4)*radius;a[i*3+1]=3.7+Math.sin(i*1.7)*radius*.38;a[i*3+2]=-2+Math.sin(i*2.4)*radius;
      }
      starsGeo.attributes.position.needsUpdate=true;
      stars.material.opacity=.25+p*.5;
    }
    arms.forEach((arm,i)=>arm.rotation.z=.45*Math.cos(phase(i,t)));
    if(id!=='s5')stars.rotation.y=t*.006;
    renderer.render(scene,camera);
  }
  return {draw,renderer};
}

function homography(corners,w,h){
  const src=[[0,0],[w,0],[w,h],[0,h]],a=[];
  for(let i=0;i<4;i++){const[x,y]=src[i],[u,v]=corners[i];a.push([x,y,1,0,0,0,-u*x,-u*y,u],[0,0,0,x,y,1,-v*x,-v*y,v]);}
  for(let col=0;col<8;col++){let pivot=col;for(let r=col+1;r<8;r++)if(Math.abs(a[r][col])>Math.abs(a[pivot][col]))pivot=r;[a[col],a[pivot]]=[a[pivot],a[col]];const scale=a[col][col];for(let c=col;c<9;c++)a[col][c]/=scale;for(let r=0;r<8;r++)if(r!==col){const f=a[r][col];for(let c=col;c<9;c++)a[r][c]-=f*a[col][c];}}
  const m=a.map(r=>r[8]);return `matrix3d(${[m[0],m[3],0,m[6],m[1],m[4],0,m[7],0,0,1,0,m[2],m[5],0,1].join(',')})`;
}

function mount(id){
  const root=document.getElementById(id+'-scene');if(!root||state[id])return;
  const canvas=root.querySelector('canvas.three');
  state[id]={root,three:canvas?make3D(id,canvas):null};
  if(id==='s7')root.querySelectorAll('.screen-pin').forEach(el=>{
    const s=screens[el.dataset.screen];if(!s)return;
    const scale=Math.max(T.width/s.width,T.height/s.height),ox=(T.width-s.width*scale)/2,oy=(T.height-s.height*scale)/2;
    const corners=s.corners.map(([x,y])=>[x*scale+ox,y*scale+oy]);el.style.transform=homography(corners,900,570);
    const index=Number(el.parentElement.dataset.role),e=T.events.find(x=>x.id===`role-${index+1}`);
    el.querySelector('h3').textContent=e.report;
  });
  draw(id,sec(T.scenes.find(s=>s.id===id).startBeat));
}

function draw(id,t){
  const s=state[id];if(!s)return;
  const root=s.root, b=beat(t),scene=T.scenes.find(x=>x.id===id);
  s.three?.draw(t);
  root.querySelectorAll('[data-super]').forEach(el=>{
    const e=T.supers.find(x=>x.id===el.dataset.super),live=active(e,t);
    const p=clamp((b-e.startBeat)/.65);
    opacity(el,live?(e.kind==='kinetic'||e.kind==='disclosure'?1:ease(p)):0);
    el.style.transform=`translateY(${live?(1-ease(p))*24:24}px)`;
  });
  if(id==='s2'){
    const cut=T.events.find(e=>e.type==='image_cut'&&active(e,t));
    opacity(query(root,'.constellation'),cut?0:1);opacity(query(root,'.manual-flow'),cut?1:0);
    const kinetic=T.supers.find(e=>e.kind==='kinetic'&&active(e,t));
    const manualIndex=kinetic?Math.min(3,Math.floor((b-31)/2)):0;
    root.querySelectorAll('.manual-step').forEach((el,i)=>el.classList.toggle('selected',i===manualIndex));
    root.querySelectorAll('.system').forEach((el,i)=>opacity(el,ease(progress(`system-label-${i+1}`,t)*12)));
    query(root,'.more').innerHTML=`+${Math.floor(26*ease(progress('system-more',t)))} <span>more</span>`;
    opacity(query(root,'.more'),since('system-more',t)?1:0);
  }
  if(id==='s1')root.querySelectorAll('.intro-sources div').forEach((el,i)=>{
    const p=ease(clamp((b-(7+i*1.7))/1.2));opacity(el,p*.9);el.style.transform=`translateY(${(1-p)*20}px)`;
  });
  if(id==='s3'){
    opacity(query(root,'.turn-symbol'),since('mark-draw',t)?1:0);
    const p=ease(progress('sync-ripple',t));
    root.querySelectorAll('.ripple').forEach((el,i)=>{el.style.transform=`scale(${.25+p*(2+i*.3)})`;opacity(el,since('sync-ripple',t)?(1-p*.8)*(.55-i*.15):0)});
    const arm=query(root,'.mark-arm');arm.style.transform=`rotate(${Math.cos(phase(0,t))*17}deg)`;
    const handoff=ease(progress('sync-ripple',t));
    root.querySelectorAll('.sync-system').forEach((el,i)=>{opacity(el,handoff);el.style.transform=`translateX(${(i<2?-1:1)*(1-handoff)*70}px)`;});
    query(root,'.sync-links').style.opacity=String(handoff*.8);
    if(active(event('turn-black-silence'),t))root.style.opacity='0';else root.style.opacity='1';
  }
  if(id==='s4'){
    const night=since('night-clock',t);
    const pipe=ease(clamp((b-58)/1.2));
    opacity(query(root,'.night'),night?ease(progress('night-clock',t)*4):0);
    opacity(query(root,'.record-stage'),night?0:1-pipe);
    opacity(query(root,'.pipeline-stage'),night?0:pipe);
    query(root,'.night-illustration').style.transform=`translateY(${-progress('night-clock',t)*8}px)`;
    const drift=progress('continuous-camera',t);
    query(root,'.record-stage').style.transform=`translateX(${-drift*40}px) scale(${1+drift*.025})`;
    root.querySelectorAll('.step').forEach((el,i)=>{const p=ease(progress(`record-card-${i+1}`,t));opacity(el,p);el.style.transform=`translate(${(1-p)*-170}px,${(1-p)*24}px) scale(${.91+.09*p})`;el.style.boxShadow=p>0&&p<1?`0 0 ${40*(1-p)}px #5EEAD455`:'';});
    const recorded=[1,2,3,4].filter(i=>b>=event(`record-card-${i}`).endBeat).length;
    query(root,'.editorheading .pill').textContent=recorded===4?'Recorded':'Recording';
    query(root,'.editorfoot span:nth-child(2)').textContent=recorded===4?'Ready to run':'Recording actions';
    query(root,'.editorfoot span:last-child').textContent=`${recorded} ${recorded===1?'step':'steps'}`;
    const selected=Math.max(0,Math.min(4,Math.floor((b-58)/2)));
    const stepDetails=[
      ['Record task','ASAP report actions captured'],
      ['Schedule','Run every night at 02:00'],
      ['Gather','Connect ASAP, GSCM, Big Data Portal and Data Hub'],
      ['Check','Inspect freshness and row counts'],
      ['AI ready','Give agents trusted evidence to investigate']
    ];
    root.querySelectorAll('.pipeline-node').forEach((el,i)=>{
      el.classList.toggle('selected',i===selected&&b>=58);
      el.classList.toggle('completed',i<selected);
    });
    query(root,'.inspector-name').textContent=stepDetails[selected][0];
    query(root,'.inspector-detail').textContent=stepDetails[selected][1];
    const actions=T.events.filter(e=>e.type==='cursor_action');let idx=actions.filter(e=>b>=e.startBeat).length-1;
    const xy=[[100,158],[352,276],[117,345],[320,345]][Math.max(0,idx)];query(root,'.cursor').style.transform=`translate(${xy[0]-355}px,${xy[1]-310}px)`;
    root.querySelectorAll('.portal-actions span,.select,.portalbody h2').forEach(el=>el.classList.remove('portal-active'));
    if(idx>=0&&idx<4){const target=[query(root,'.portalbody h2'),query(root,'.select'),query(root,'.run-button'),query(root,'.download-button')][idx];target?.classList.add('portal-active');}
  }
  if(id==='s5'){
    root.querySelectorAll('.data-lines i').forEach((el,i)=>{const p=ease(clamp((b-(74+i*2))/1.4));el.style.width=`${p*100}%`;opacity(el,p*.55);});
    root.querySelectorAll('.run-lane span').forEach((el,i)=>el.classList.toggle('done',b>=72+i*2));
    opacity(query(root,'.history'),ease(progress('run-history',t)));
    root.querySelectorAll('tbody tr').forEach((el,i)=>{const p=ease(progress(`run-row-${i+1}`,t));opacity(el,p);const e=event(`run-row-${i+1}`);el.querySelector('.rows-count').textContent=Math.floor(e.rows*p).toLocaleString('en-US');});
  }
  if(id==='s6'){
    const report=b>=108&&!since('email-send',t),email=since('report-email',t)&&!since('audit-card',t),audit=since('audit-card',t);
    opacity(query(root,'.ask'),b>=102?0:1);
    opacity(query(root,'.agent-investigation'),b>=102&&b<108?ease(clamp((b-102)/1.2)):0);
    root.querySelectorAll('.agent-evidence>div').forEach((el,i)=>{
      const p=ease(clamp((b-(103+i*1.2))/.8));opacity(el,p);el.style.transform=`translateY(${(1-p)*20}px)`;
    });
    opacity(query(root,'.agent-outcome'),ease(clamp((b-106.4)/.8)));
    const question=event('ask-type').text;query(root,'.question').textContent=question.slice(0,Math.floor(question.length*progress('ask-type',t)));
    opacity(query(root,'.report'),report?ease(clamp((b-108)/1.4))*(email?1-ease(progress('report-email',t)*2):1):0);
    query(root,'.report').style.transform=`perspective(2500px) rotateX(${2+progress('report-email',t)*48}deg) scale(${.97+.03*ease(progress('report-build',t))-progress('report-email',t)*.3})`;
    opacity(query(root,'.email'),email?ease((progress('report-email',t)-.5)*2):0);opacity(query(root,'.audit'),audit?ease(progress('audit-card',t)*3):0);
    const line=query(root,'.chart-line');line.style.strokeDasharray='1300';line.style.strokeDashoffset=String(1300*(1-ease(progress('report-line',t))));
    const insight=event('report-insight').text,words=insight.split(' ');query(root,'.insight').textContent=words.slice(0,Math.ceil(words.length*progress('report-insight',t))).join(' ');
    opacity(query(root,'.sources'),since('report-sources',t)?1:0);
    root.querySelectorAll('.kpis>div').forEach((el,i)=>{const p=ease(clamp((progress('report-kpis',t)*3-i)*.8));opacity(el,p);el.style.transform=`translateY(${(1-p)*18}px)`;});
  }
  if(id==='s7'){
    root.querySelectorAll('.role-photo').forEach((el,i)=>{const e=event(`role-${i+1}`);const on=active(e,t),p=ease(progress(e.id,t));opacity(el,on?1:0);el.querySelector('.role-art').style.transform=`translateY(${(1-p)*18}px)`;el.querySelector('.role-screen').style.transform=`translateY(${(1-p)*12}px)`;});
  }
  if(id==='s8'){
    const lock=since('logo-lockup',t);opacity(query(root,'.lockup'),lock?ease(progress('logo-lockup',t)*4):0);
    opacity(query(root,'.end-arm'),since('final-tick',t)?0:1);
    query(root,'.end-mark .mark-arm').style.transform=`rotate(${18*Math.cos(t*Math.PI*2)}deg)`;
    if(since('final-black',t))root.style.opacity='0';else root.style.opacity='1';
  }
}
function bridge(t){
  const el=document.querySelector('.camera-bridge');if(!el)return;
  const boundaries=[T.scenes.find(s=>s.id==='s4').endBeat,T.scenes.find(s=>s.id==='s5').endBeat].map(sec);
  const hit=boundaries.map(at=>(t-(at-.42))/.84).find(x=>x>=0&&x<=1);
  if(hit===undefined){el.style.opacity='0';return;}
  const cover=Math.min(.22,Math.sin(Math.PI*hit)*.22);
  el.style.opacity=String(cover);
  el.style.transform='none';
  el.style.filter='none';
}
function seek(t){for(const s of T.scenes){if(t>=sec(s.startBeat)&&t<=sec(s.endBeat)){if(!state[s.id])mount(s.id);draw(s.id,t);}}bridge(t);}
window.Film={mount,draw,seek,bridge,timeline:T};
