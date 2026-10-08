const introClamp=x=>Math.max(0,Math.min(1,x));
const introEase=x=>1-Math.pow(1-introClamp(x),3);
const introText=(x,y,text,size=30,fill='#173239',weight=400,anchor='middle')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" text-anchor="${anchor}">${text}</text>`;

export function etlSVG(time,cues){
 const p=key=>introEase((time-cues[key])/.55),extract=p('extract'),transform=p('transform'),load=p('load');
 const file=(x,y,title,sub,color)=>`<g transform="translate(${x} ${y})"><path d="M0 0h130l40 40v205H0Z" fill="#fff" stroke="${color}" stroke-width="3"/><path d="M130 0v40h40" fill="none" stroke="${color}" stroke-width="3"/>${introText(85,95,title,29,color,500)}${introText(85,139,sub,21)}<path d="M35 173h100M35 194h82" stroke="#cbd7d3" stroke-width="4"/></g>`;
 const arrow=(x,p)=>`<g opacity="${p}"><path d="M${x} 595h170" stroke="#2f806c" stroke-width="4" stroke-dasharray="170" stroke-dashoffset="${170*(1-p)}"/><path d="m${x+158} 583 14 12-14 12" fill="none" stroke="#2f806c" stroke-width="4"/></g>`;
 return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" font-family="Outfit, sans-serif" color="#173239">
 <defs><radialGradient id="etl-bg" cx="80%" cy="75%" r="75%"><stop stop-color="#e8f2ef"/><stop offset="1" stop-color="#f8faf9"/></radialGradient></defs><rect width="1920" height="1080" fill="url(#etl-bg)"/>
 ${introText(960,160,'Downloads are only the start.',63,'#173239',500)}
 ${introText(325,345,'Extract',48,extract?'#0d7377':'#657974',500)}${introText(960,345,'Transform',48,transform?'#0d7377':'#657974',500)}${introText(1595,345,'Load',48,load?'#0d7377':'#657974',500)}
 <g transform="translate(0 ${-extract*16})">${file(170,455,'ASAP','Sales report','#417b93')}${file(330,495,'GSCM','Stock report','#6a7199')}</g>
 ${arrow(565,extract)}
 <g opacity="${.3+.7*extract}"><rect x="795" y="447" width="330" height="78" rx="10" fill="#fff" stroke="#cbd7d3"/>${introText(960,498,'Sep 2026   /   2026.09',28)}<path d="M960 545v45m-12-12 12 12 12-12" fill="none" stroke="#78998f" stroke-width="3"/><rect x="795" y="615" width="330" height="95" rx="10" fill="${transform?'#dceee4':'#fff'}" stroke="${transform?'#539c81':'#cbd7d3'}" stroke-width="2"/>${introText(960,677,'2026-09',42,'#215d4a',500)}</g>
 ${arrow(1185,transform)}
 <g opacity="${.3+.7*load}"><path d="M1485 475v212c0 65 220 65 220 0V475" fill="${load?'#dceee4':'#fff'}" stroke="#368477" stroke-width="5"/><ellipse cx="1595" cy="475" rx="110" ry="35" fill="#fff" stroke="#368477" stroke-width="5"/><path d="M1485 547c0 65 220 65 220 0M1485 617c0 65 220 65 220 0" fill="none" stroke="#368477" stroke-width="4"/></g>
 ${introText(325,815,'Download reports',29,'#49665c')}${introText(960,815,'Standardize formats',29,'#49665c')}${introText(1595,815,'Save to your destination',29,'#49665c')}
 <g opacity="${load}">${introText(960,969,'Analysis + scheduled reporting',39,'#215d4a',500)}</g>
 </svg>`;
}

export function capabilitySVG(time,cues){
 const labels=['Build','Schedule','Run now','Run history'],keys=['build','schedule','trigger','track'];
 const icons=['M4 12h8v8H4Zm16 0h8v8h-8ZM12 16h8M16 16V4h8','M16 3a13 13 0 1 0 0 26 13 13 0 0 0 0-26Zm0 6v8l6 4','m9 4 19 12L9 28Z','M5 9a12 12 0 1 1-1 12M4 3v8h8M16 9v8l6 4'];
 return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="150" font-family="Outfit, sans-serif">${labels.map((label,i)=>{const p=introEase((time-cues[keys[i]])/.35),x=365+i*310;return `<g opacity="${p}" transform="translate(${x} ${12*(1-p)})"><rect y="25" width="280" height="90" rx="12" fill="#fff" stroke="#aacbbb"/><svg x="25" y="51" width="38" height="38" viewBox="0 0 32 32" fill="none" stroke="#0d7377" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[i]}"/></svg>${introText(84,82,label,31,'#215d4a',500,'start')}</g>`}).join('')}</svg>`;
}
