"""Voice narration lines into a line bank that audio/remix.py places, without re-voicing lines in other banks.

  python audio/voice.py rev14

Every line in narration.json whose "voice" is the bank's name is synthesized with the film's voice
(en-US-AndrewMultilingualNeural at -3%, as audio/score.py), trimmed like score.py (a short lead and tail around the speech) and written one after another to
audio/voice-<bank>.flac, with audio/voice-<bank>.json giving each line's [start, end] in that track and every word's offset
from its line start (the synthesizer's word boundaries). Needs edge-tts and network access to its speech service."""
import asyncio, json, os, shutil, subprocess, sys
from pathlib import Path
import edge_tts, numpy as np

FILM = Path(__file__).resolve().parents[1]
FF = shutil.which('ffmpeg')
VOICE, RATE, rate, GAP = 'en-US-AndrewMultilingualNeural', '-3%', 48000, 0.5
bank = sys.argv[1]
lines = [s for s in json.loads((FILM / 'narration.json').read_text(encoding='utf-8')) if s.get('voice') == bank]
if not lines:
    raise SystemExit(f'No line in narration.json has "voice": "{bank}".')


async def synthesize(text, tries=4):
    for attempt in range(tries):  # the service now and then closes a stream without audio; ask again
        audio, words = bytearray(), []
        try:
            async for part in edge_tts.Communicate(text, VOICE, rate=RATE, boundary='WordBoundary',
                                                   proxy=os.environ.get('HTTPS_PROXY')).stream():
                if part['type'] == 'audio':
                    audio += part['data']
                elif part['type'] == 'WordBoundary':
                    words.append((part['text'], part['offset'] / 1e7))
            return bytes(audio), words
        except edge_tts.exceptions.NoAudioReceived:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))


track, entries, at = [], [], 0.0
for s in lines:
    text = s.get('spoken', s['en'])
    mp3, words = asyncio.run(synthesize(text))
    pcm = subprocess.run([FF, '-v', 'error', '-i', 'pipe:0', '-f', 'f32le', '-ar', str(rate), '-ac', '2', '-'],
                         input=mp3, capture_output=True, check=True).stdout
    a = np.frombuffer(pcm, np.float32).reshape(-1, 2)
    active = np.flatnonzero(np.max(np.abs(a), axis=1) > .004)
    lead = max(0, active[0] - 2400)
    a = a[lead:min(len(a), active[-1] + 4800)]
    cues = {}
    for word, offset in words:  # first occurrence of each word, as score.py's cue lookup finds it
        cues.setdefault(word, round(offset - lead / rate, 6))
    entries.append({'spoken': text, 'start': round(at, 6), 'end': round(at + len(a) / rate, 6), 'cues': cues})
    track.append(a); track.append(np.zeros((round(GAP * rate), 2), np.float32))
    at += len(a) / rate + GAP
    print(f'{text}  {len(a) / rate:.2f} s')

wav = FILM / '.audio-cache' / f'voice-{bank}.wav'
wav.parent.mkdir(exist_ok=True)
subprocess.run([FF, '-v', 'error', '-y', '-f', 'f32le', '-ar', str(rate), '-ac', '2', '-i', 'pipe:0', str(wav)],
               input=np.concatenate(track).tobytes(), check=True)
subprocess.run([FF, '-v', 'error', '-y', '-i', str(wav), '-c:a', 'flac', str(FILM / f'audio/voice-{bank}.flac')], check=True)
(FILM / f'audio/voice-{bank}.json').write_text(json.dumps({
    'source': f'Narration lines voiced for {bank} ({VOICE}, {RATE}), audio/voice-{bank}.flac',
    'note': 'Each line occupies [start, end] seconds of the track; cue offsets are seconds from the line start, from the '
            'synthesizer word boundaries. Written by audio/voice.py.',
    'lines': entries}, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
