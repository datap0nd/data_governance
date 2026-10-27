import fs from 'node:fs';
import path from 'node:path';
import {messScene,joinScene,screenScene,endScene,blurDefs,windowSlots,fileSlots,ringSlots} from '../compositions/wizard.js';
const base=path.resolve(import.meta.dirname,'..'); process.chdir(base);
const narration=JSON.parse(fs.readFileSync('narration.json','utf8'));
// Word-boundary cues come from audio/remix.py (or audio/score.py); before the first voice pass, each cue falls back to its segment start.
const cueFile='assets/cues.json';
const cues=fs.existsSync(cueFile)?JSON.parse(fs.readFileSync(cueFile,'utf8')).cues:{};
for(const segment of narration)for(const key of Object.keys(segment.cues||{}))if(!(key in cues)){cues[key]=segment.start;console.warn(`Cue ${key} has no voice timing yet; using its segment start.`)}
// The real Wizard screen clips and their camera moves, from scripts/screen.py.
const screen=JSON.parse(fs.readFileSync('assets/screen.json','utf8'));
// Scenes cut just before the line that introduces them: the problem, Wizard, the real app, the close.
const duration=Math.ceil(narration.at(-1).end+2.5);
const cut=i=>Math.round((narration[i].start-.3)*10)/10;
const line=text=>narration.findIndex(s=>s.en.startsWith(text));
const scenes=[['sMess',0,cut(line('Meet Wizard')),'Too many places to look'],['sJoin',cut(line('Meet Wizard')),cut(line('So you can simply ask')),'Everything together in Wizard'],
 ['sApp',cut(line('So you can simply ask')),cut(line('Stop searching')),'The real Wizard: ask, timelapse, check'],['sEnd',cut(line('Stop searching')),duration,'Every source. One answer.']].map(([id,start,end,name])=>({id,start,end,name}));
const app=scenes[2];
for(const [id,c] of Object.entries(screen.clips))if(c.start<app.start-1e-6||c.end>app.end+1e-6)throw new Error(`Screen clip ${id} (${c.start}–${c.end}) runs outside the app scene (${app.start}–${app.end}).`);
// Motion: when each move starts, read by compositions/runtime.js (the picture) and audio/sfx.py (its whoosh), so the
// two cannot drift apart. Times are film seconds.
const sc=Object.fromEntries(scenes.map(s=>[s.id,s])),clips=screen.clips;
const motion={
 portals:[.4,.85,1.3,1.75],                                  // the portal windows drop in
 files:[cues.excel,cues.emails,cues.presentations,cues.pdfs],  // file cards land in pairs on their words
 rush:cues.packed-.2,                                         // every list rushes past on "packed with reports"
 question:narration[line('Finding one answer')].start-.55,     // the hunt's question types in, large
 dock:cues.look-1.0,                                          // and flies up into its search bar before the hunt
 gather:sc.sMess.end-.8,                                      // everything sweeps to the centre
 name:cues.meetWizard-.1,                                     // "Wizard" pops in
 join:cues.together-.5,                                       // every source flows into the logo
 grow:sc.sApp.start,                                          // the app grows out of the logo
 slide:clips.stock.start+.2,                                  // the second question whips in
 lapses:Object.values(clips).flatMap(c=>c.timelapses.map(l=>l.from)),
 safe:cues.safe,                                              // "Secure by design"
 away:sc.sApp.end-.6,                                         // the app pulls away
 say:{stop:cues.stop-.05,start:cues.start-.05},               // "Stop searching." "Start asking."
 logo:cues.endWizard-.3,                                      // the statement whips off, the logo punches in
 tagline:cues.oneAnswer-.1                                    // "Every source. One answer."
};
// Whooshes for those moves (audio/sfx.py synthesizes and mixes them): kind, film time, level in dB, pan from → to.
const sfx=[...motion.portals.map((t,i)=>({kind:'swish',t,gain:-19,pan:[[-.5,-.2],[-.2,.1],[.1,.3],[.3,.5]][i]})),
 ...motion.files.map((t,i)=>({kind:'swish',t:t-.15,gain:-22,pan:[[-.4,-.4],[-.1,-.1],[.2,.2],[.4,.4]][i]})),
 {kind:'rush',t:motion.rush,gain:-18,pan:[0,0]},
 {kind:'swish',t:motion.question,gain:-19,pan:[0,0]},{kind:'whoosh',t:motion.dock,gain:-17,pan:[0,0]},
 {kind:'whoosh',t:motion.gather,gain:-18,pan:[0,0]},{kind:'boom',t:sc.sMess.end-.05,gain:-18,pan:[0,0]},
 {kind:'swish',t:motion.name,gain:-18,pan:[0,0]},{kind:'whoosh',t:motion.join,gain:-17,pan:[0,0]},
 {kind:'whoosh',t:motion.grow-.05,gain:-16,pan:[0,0]},{kind:'boom',t:motion.grow+.35,gain:-17,pan:[0,0]},
 {kind:'whip',t:motion.slide-.08,gain:-15,pan:[.7,-.7]},
 ...motion.lapses.map(t=>({kind:'riser',t:t-.1,gain:-20,pan:[0,0]})),
 {kind:'swish',t:motion.safe-.1,gain:-18,pan:[0,0]},{kind:'whoosh',t:motion.away,gain:-18,pan:[0,0]},
 {kind:'swish',t:motion.say.stop,gain:-20,pan:[0,0]},{kind:'swish',t:motion.say.start,gain:-20,pan:[0,0]},
 {kind:'whoosh',t:motion.logo-.05,gain:-16,pan:[0,0]},{kind:'boom',t:motion.logo+.3,gain:-15,pan:[0,0]},
 {kind:'swish',t:motion.tagline,gain:-20,pan:[0,0]}].sort((a,b)=>a.t-b.t);
const scene=(id,content)=>`<section class="scene" id="${id}">${content}</section>`;
const html=scene('sMess',messScene())+scene('sJoin',joinScene())+scene('sApp',screenScene(screen.clips))+scene('sEnd',endScene());
const config={duration,scenes,narration,cues,screen,motion,layout:{windows:windowSlots,files:fileSlots,ring:ringSlots}};
const css=fs.readFileSync('compositions/film.css','utf8'),runtime=fs.readFileSync('compositions/runtime.js','utf8');
const title='Wizard — Every source, one answer';
fs.writeFileSync('timeline.json',JSON.stringify({title,version:13,durationSeconds:duration,width:1920,height:1080,fps:30,...config,sfx},null,2)+'\n');
fs.writeFileSync('index.html',`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title><style>${css}</style><script src="assets/vendor/gsap.min.js"></script></head><body><main id="film" data-composition-id="metronome-your-data-connected" data-width="1920" data-height="1080" data-duration="${duration}" data-fps="30">${blurDefs()}${html}<audio id="narration-and-music" src="audio/score.wav" data-start="0" data-duration="${duration}" data-track-index="10"></audio></main><script>window.FILM_CONFIG=${JSON.stringify(config)};${runtime}</script></body></html>`);
fs.writeFileSync('assets/build-data.json',JSON.stringify(config));console.log(`Built ${duration}-second film, ${scenes.length} scenes, ${Object.keys(screen.clips).length} screen clips, no subtitles.`);
