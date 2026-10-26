import fs from 'node:fs';
import path from 'node:path';
import {messScene,joinScene,screenScene,endScene,windowSlots,fileSlots,ringSlots} from '../compositions/wizard.js';
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
const scene=(id,content)=>`<section class="scene" id="${id}">${content}</section>`;
const html=scene('sMess',messScene())+scene('sJoin',joinScene())+scene('sApp',screenScene(screen.clips))+scene('sEnd',endScene());
const config={duration,scenes,narration,cues,screen,layout:{windows:windowSlots,files:fileSlots,ring:ringSlots}};
const css=fs.readFileSync('compositions/film.css','utf8'),runtime=fs.readFileSync('compositions/runtime.js','utf8');
const title='Wizard — Every source, one answer';
fs.writeFileSync('timeline.json',JSON.stringify({title,version:13,durationSeconds:duration,width:1920,height:1080,fps:30,...config},null,2)+'\n');
fs.writeFileSync('index.html',`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title><style>${css}</style><script src="assets/vendor/gsap.min.js"></script></head><body><main id="film" data-composition-id="metronome-your-data-connected" data-width="1920" data-height="1080" data-duration="${duration}" data-fps="30">${html}<audio id="narration-and-music" src="audio/score.wav" data-start="0" data-duration="${duration}" data-track-index="10"></audio></main><script>window.FILM_CONFIG=${JSON.stringify(config)};${runtime}</script></body></html>`);
fs.writeFileSync('assets/build-data.json',JSON.stringify(config));console.log(`Built ${duration}-second film, ${scenes.length} scenes, ${Object.keys(screen.clips).length} screen clips, no subtitles.`);
