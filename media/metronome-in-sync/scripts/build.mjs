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
// Motion: when each move starts, read by compositions/runtime.js. Times are film seconds.
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
 handoff:clips.stock.start,                                   // the second question cross-fades in
 safe:cues.safe,                                              // "Secure by design"
 away:sc.sApp.end-.9,                                         // the app pulls away
 say:{stop:cues.stop-.05,start:cues.start-.05},               // "Stop searching." "Start asking."
 logo:cues.endWizard-.3,                                      // the statement whips off, the logo punches in
 tagline:cues.oneAnswer-.1                                    // "Every source. One answer."
};
const scene=(id,content)=>`<section class="scene" id="${id}">${content}</section>`;
const html=scene('sMess',messScene())+scene('sJoin',joinScene())+scene('sApp',screenScene(screen.clips))+scene('sEnd',endScene());
const config={duration,scenes,narration,cues,screen,motion,layout:{windows:windowSlots,files:fileSlots,ring:ringSlots}};
const css=fs.readFileSync('compositions/film.css','utf8'),runtime=fs.readFileSync('compositions/runtime.js','utf8');
const title='Wizard — Every source, one answer';
fs.writeFileSync('timeline.json',JSON.stringify({title,version:14,durationSeconds:duration,width:1920,height:1080,fps:30,...config},null,2)+'\n');
const page=(lang,extraCss,extraHtml,cfg)=>`<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><title>${title}</title><style>${css}${extraCss}</style><script src="assets/vendor/gsap.min.js"></script></head><body><main id="film" data-composition-id="metronome-your-data-connected" data-width="1920" data-height="1080" data-duration="${duration}" data-fps="30">${blurDefs()}${html}${extraHtml}<audio id="narration-and-music" src="audio/score.wav" data-start="0" data-duration="${duration}" data-track-index="10"></audio></main><script>window.FILM_CONFIG=${JSON.stringify(cfg)};${runtime}</script></body></html>`;
fs.writeFileSync('index.html',page('en','','',config));
// The Korean-subtitled version (scripts/subtitles.py): the same film with the subtitles drawn over it.
if(fs.existsSync('assets/subtitles-ko.json'))fs.writeFileSync('index-ko.html',page('ko',fs.readFileSync('compositions/subtitles.css','utf8'),'<div class="subtitle" data-layout-allow-overlap><span></span></div>',{...config,subtitles:JSON.parse(fs.readFileSync('assets/subtitles-ko.json','utf8'))}));
fs.writeFileSync('assets/build-data.json',JSON.stringify(config));console.log(`Built ${duration}-second film, ${scenes.length} scenes, ${Object.keys(screen.clips).length} screen clips, no subtitles.`);
