import os, shutil, asyncio, json, subprocess, wave, hashlib
from pathlib import Path
import edge_tts, imageio_ffmpeg, numpy as np
FF=os.environ.get('FILM_FFMPEG_PATH') or shutil.which('ffmpeg') or imageio_ffmpeg.get_ffmpeg_exe()
FILM=Path(__file__).resolve().parents[1]
segments=json.loads((FILM/'narration.json').read_text(encoding='utf-8'))
tmp=FILM/'.audio-cache';tmp.mkdir(exist_ok=True)
def voice_path(i,s):
 key=hashlib.sha256(('en-US-AndrewMultilingualNeural|-3%|'+s['en']).encode()).hexdigest()[:16]
 return tmp/f'{i:02}-{key}.mp3'
async def voice(i,s):
 p=voice_path(i,s)
 if not p.exists():
  await edge_tts.Communicate(s['en'],'en-US-AndrewMultilingualNeural',rate='-3%').save(str(p))
async def main():
 for i,s in enumerate(segments):
  await voice(i,s)
  print(f'Voice {i+1}/{len(segments)}',flush=True)
asyncio.run(main())
rate=48000
mix=np.zeros((84*rate,2),np.float32)
timing=[]
for i,s in enumerate(segments):
 p=voice_path(i,s)
 b=subprocess.check_output([FF,'-v','error','-i',str(p),'-f','f32le','-ar',str(rate),'-ac','2','-'])
 a=np.frombuffer(b,dtype=np.float32).reshape(-1,2)
 # Keep a short lead/tail; align captions to the audible sentence.
 mono=np.max(np.abs(a),axis=1);active=np.flatnonzero(mono>.004)
 if len(active):a=a[max(0,active[0]-2400):min(len(a),active[-1]+4800)]
 dur=len(a)/rate;budget=s['end']-s['start'];speed=max(1,dur/budget)
 raw=tmp/f'{i:02}.wav'
 with wave.open(str(raw),'wb') as w:w.setparams((2,2,rate,0,'NONE','not compressed'));w.writeframes((np.clip(a,-1,1)*32767).astype(np.int16).tobytes())
 if speed>1:
  b=subprocess.check_output([FF,'-v','error','-i',str(raw),'-af',f'atempo={speed:.6f}','-f','f32le','-ar',str(rate),'-ac','2','-'])
  a=np.frombuffer(b,dtype=np.float32).reshape(-1,2)
 start=round(s['start']*rate);n=min(len(a),round(budget*rate));mix[start:start+n]+=a[:n]
 timing.append({'segment':i+1,'start':s['start'],'end':s['start']+n/rate,'speed':round(speed,3),'naturalSeconds':round(dur,3)})
vo=tmp/'narration.wav'
with wave.open(str(vo),'wb') as w:w.setparams((2,2,rate,0,'NONE','not compressed'));w.writeframes((np.clip(mix,-1,1)*32767).astype(np.int16).tobytes())
subprocess.run([FF,'-y','-i',str(vo),'-c:a','flac',str(FILM/'audio/narration.flac'),'-loglevel','error'],check=True)
music=FILM/'audio/background.mp3'
# Speech remains clearly above a continuous instrumental bed.
subprocess.run([FF,'-y','-i',str(vo),'-i',str(music),'-filter_complex','[0:a]loudnorm=I=-17:TP=-3:LRA=7,asplit=2[v][sc];[1:a]atrim=0:84,asetpts=PTS-STARTPTS,volume=0.12,afade=t=in:d=1,afade=t=out:st=80:d=4[m];[m][sc]sidechaincompress=threshold=0.018:ratio=4:attack=30:release=600[duck];[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[mix]','-map','[mix]','-ar','48000','-ac','2','-c:a','pcm_s24le',str(FILM/'audio/score.wav'),'-loglevel','error'],check=True)
(FILM/'audio/narration-timing.json').write_text(json.dumps(timing,indent=2),encoding='utf-8')
def tc(t):
 ms=round(t*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000);return f'{h:02}:{m:02}:{s:02},{ms:03}'
for lang in ['en','ko']:
 (FILM/f'subtitles.{lang}.srt').write_text('\n\n'.join(f'{i+1}\n{tc(s["start"])} --> {tc(s["end"])}\n{s[lang]}' for i,s in enumerate(segments))+'\n',encoding='utf-8',newline='\n')
print(json.dumps(timing,indent=2))
