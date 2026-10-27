"""Mix the narration from recorded voice lines, without a speech service.

Each line in narration.json is cut from the line bank its "voice" names: audio/voice-<voice>.flac, located by
audio/voice-<voice>.json (rev12: the revision 12 recordings; a later bank: new lines voiced by audio/voice.py with the same
voice and settings), and placed at its new start. Word cues move with their line. The music bed and ducking are the same
as audio/score.py, which re-voices every line instead.

  python audio/remix.py            # every line must have its recording
  python audio/remix.py --draft    # lines not voiced yet stay silent; their length and word cues are estimated

A draft is for timing the picture while a line waits for its voice: never release one."""
import json, math, re, shutil, subprocess, sys, wave
from pathlib import Path
import numpy as np
import bed

FILM = Path(__file__).resolve().parents[1]
FF = shutil.which('ffmpeg')
rate = 48000
draft = '--draft' in sys.argv[1:]
segments = json.loads((FILM / 'narration.json').read_text(encoding='utf-8'))
duration = math.ceil(segments[-1]['end'] + 2.5)  # as scripts/build.mjs computes the film's length


def estimate(text):
    """(seconds, {word: offset}) for an unvoiced line: the film's voice at -3% takes about 0.22 s a syllable, 0.48 s a
    comma or colon and 0.64 s a full stop inside the line (fitted to the revision 12 lines, within 0.8 s)."""
    tokens = re.findall(r"[A-Za-z0-9'’-]+[,:;.?!]?", text)
    at, cues = 0.0, {}
    for k, token in enumerate(tokens):
        word = token.rstrip(',:;.?!')
        cues.setdefault(word, round(max(0.0, at - 0.1), 3))
        spelled = word.isupper() and len(word) <= 5 and word != 'ASAP'
        at += 0.218 * (len(word.replace('-', '')) if spelled else
                       max(1, len(re.findall(r'[aeiouy]+', word.lower())) - (word.lower().endswith('e') and len(word) > 3)))
        if k < len(tokens) - 1:
            at += {',': .479, ':': .479, ';': .479, '.': .635, '?': .635, '!': .635}.get(token[-1], 0)
    return round(max(0.5, at - 0.48), 3), cues


banks = {}
for name in sorted({s.get('voice') for s in segments if s.get('voice')}):
    flac = FILM / f'audio/voice-{name}.flac'
    if not flac.exists() and draft:
        continue
    raw = subprocess.check_output([FF, '-v', 'error', '-i', str(flac), '-f', 'f32le', '-ar', str(rate), '-ac', '2', '-'])
    banks[name] = (json.loads((FILM / f'audio/voice-{name}.json').read_text(encoding='utf-8'))['lines'],
                   np.frombuffer(raw, dtype=np.float32).reshape(-1, 2))

mix = np.zeros((duration * rate, 2), np.float32)
timing, cues, provenance, problems, unvoiced = [], {}, [], [], []
for i, s in enumerate(segments):
    spoken = s.get('spoken', s['en'])
    bank, track = banks.get(s.get('voice'), ([], None))
    line = next((b for b in bank if b['spoken'] == spoken), None)
    if line is None and draft:
        length, offsets = estimate(spoken)
        line = {'cues': offsets, 'start': None}
        unvoiced.append(i + 1)
    elif line is None:
        problems.append(f'Segment {i + 1} has no recording in voice bank {s.get("voice")!r}; voice it with audio/voice.py.')
        continue
    else:
        clip = track[round(line['start'] * rate):round(line['end'] * rate)]
        length = len(clip) / rate
    if length > s['end'] - s['start'] + 1e-3:
        problems.append(f'Segment {i + 1} needs {length:.3f}s but has {s["end"] - s["start"]:.3f}s.')
        continue
    if line['start'] is not None:
        at = round(s['start'] * rate)
        mix[at:at + len(clip)] += clip
    timing.append({'segment': i + 1, 'start': s['start'], 'end': round(s['start'] + length, 6), 'speed': 1,
                   'naturalSeconds': round(length, 3), 'voice': s['voice'] if line['start'] is not None else 'estimate',
                   'sourceStart': line['start']})
    for key, word in s.get('cues', {}).items():
        if word not in line['cues']:
            problems.append(f'Segment {i + 1}: cue word {word!r} has no boundary in its voice bank.')
            continue
        cues[key] = round(s['start'] + line['cues'][word], 6)
        provenance.append({'key': key, 'word': word, 'segment': i + 1, 'offset': line['cues'][word], 'filmTime': cues[key]})
if problems:
    raise SystemExit('\n'.join(problems))

(FILM / 'assets/cues.json').write_text(json.dumps({'cues': cues, 'provenance': provenance}, indent=2) + '\n', encoding='utf-8')
cache = FILM / '.audio-cache'; cache.mkdir(exist_ok=True)
vo = cache / 'narration.wav'
with wave.open(str(vo), 'wb') as w:
    w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
    w.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())
subprocess.run([FF, '-y', '-i', str(vo), '-c:a', 'flac', str(FILM / 'audio/narration.flac'), '-loglevel', 'error'], check=True)
music = FILM / 'audio/background.mp3'
# Same bed as audio/score.py (audio/bed.py): speech stays clearly above a continuous instrumental bed. The voice is padded
# to the film's length after loudnorm, whose sidechain copy otherwise ends shortly after the last word and cuts the music.
subprocess.run([FF, '-y', '-i', str(vo), '-i', str(music), '-filter_complex',
                f'[0:a]loudnorm=I=-17:TP=-3:LRA=7,aresample=48000,apad=whole_dur={duration},asplit=2[v][sc];'
                f'{bed.music_filter(duration, bed.seconds(FF, music))};'
                '[m][sc]sidechaincompress=threshold=0.018:ratio=4:attack=30:release=600[duck];'
                '[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[mix]',
                '-map', '[mix]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', str(FILM / 'audio/score.wav'), '-loglevel', 'error'], check=True)
(FILM / 'audio/narration-timing.json').write_text(json.dumps(timing, indent=2), encoding='utf-8')
print(json.dumps(timing, indent=2))
if unvoiced:
    print(f'DRAFT: segments {unvoiced} have no recording yet and are silent; their timing is estimated. Voice them with '
          'audio/voice.py before release.')
