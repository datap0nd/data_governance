import os, shutil, asyncio, json, subprocess, wave, hashlib
from pathlib import Path
import edge_tts, imageio_ffmpeg, numpy as np
FF=os.environ.get('FILM_FFMPEG_PATH') or shutil.which('ffmpeg') or imageio_ffmpeg.get_ffmpeg_exe()
FILM=Path(__file__).resolve().parents[1]
segments=json.loads((FILM/'narration.json').read_text(encoding='utf-8'))
duration=json.loads((FILM/'timeline.json').read_text(encoding='utf-8'))['durationSeconds']
tmp=FILM/'.audio-cache';tmp.mkdir(exist_ok=True)
def voice_path(i,s):
 key=hashlib.sha256(('word-boundaries-v1|en-US-AndrewNeural|-3%|'+s.get('spoken',s['en'])).encode()).hexdigest()[:16]
 return tmp/f'{i:02}-{key}.mp3'
async def voice(i,s):
 p=voice_path(i,s)
 if not p.exists() or not p.with_suffix('.json').exists():
  boundaries=[]
  with p.open('wb') as out:
   async for part in edge_tts.Communicate(s.get('spoken',s['en']),'en-US-AndrewNeural',rate='-3%',boundary='WordBoundary').stream():
    if part['type']=='audio':out.write(part['data'])
    elif part['type']=='WordBoundary':boundaries.append(part)
  p.with_suffix('.json').write_text(json.dumps(boundaries,indent=2),encoding='utf8')
async def main():
 for i,s in enumerate(segments):
  await voice(i,s)
  print(f'Voice {i+1}/{len(segments)}',flush=True)
asyncio.run(main())
rate=48000
mix=np.zeros((duration*rate,2),np.float32)
timing=[]
for i,s in enumerate(segments):
 p=voice_path(i,s)
 b=subprocess.check_output([FF,'-v','error','-i',str(p),'-f','f32le','-ar',str(rate),'-ac','2','-'])
 a=np.frombuffer(b,dtype=np.float32).reshape(-1,2)
 # Keep a short lead/tail without changing the sentence's natural delivery.
 mono=np.max(np.abs(a),axis=1);active=np.flatnonzero(mono>.004)
 if len(active):a=a[max(0,active[0]-2400):min(len(a),active[-1]+4800)]
 dur=len(a)/rate;budget=s['end']-s['start']
 if dur>budget:
  raise ValueError(f'Segment {i+1} needs {dur:.3f}s but has {budget:.3f}s. Revise the sentence or timing; do not accelerate or truncate speech.')
 start=round(s['start']*rate);n=len(a);mix[start:start+n]+=a
 timing.append({'segment':i+1,'start':s['start'],'end':s['start']+n/rate,'speed':1,'naturalSeconds':round(dur,3)})
vo=tmp/'narration.wav'
with wave.open(str(vo),'wb') as w:w.setparams((2,2,rate,0,'NONE','not compressed'));w.writeframes((np.clip(mix,-1,1)*32767).astype(np.int16).tobytes())
subprocess.run([FF,'-y','-i',str(vo),'-c:a','flac',str(FILM/'audio/narration.flac'),'-loglevel','error'],check=True)
music=FILM/'audio/background.mp3'
# Speech remains clearly above a continuous instrumental bed.
subprocess.run([FF,'-y','-i',str(vo),'-stream_loop','-1','-i',str(music),'-filter_complex',f'[0:a]loudnorm=I=-17:TP=-3:LRA=7,asplit=2[v][sc];[1:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume=0.12,afade=t=in:d=1,afade=t=out:st={duration-4}:d=4[m];[m][sc]sidechaincompress=threshold=0.018:ratio=4:attack=30:release=600[duck];[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[mix]','-map','[mix]','-ar','48000','-ac','2','-c:a','pcm_s24le',str(FILM/'audio/score.wav'),'-loglevel','error'],check=True)
(FILM/'audio/narration-timing.json').write_text(json.dumps(timing,indent=2),encoding='utf-8')
print(json.dumps(timing,indent=2))
