"""Whooshes for the film's moves, synthesized here (no sample library), and the final mix.

scripts/build.mjs lists every move's sound in timeline.json ("sfx": kind, film time, level in dB, pan from -> to), from
the same times compositions/runtime.js animates, so picture and sound cannot drift. Each sound is noise shaped by a
band that sweeps through it (a swish, whoosh, whip, rush or riser) or a falling sine (a boom), panned and seeded so the
result is the same every run. They are added to the voice-and-music premix that audio/remix.py (or audio/score.py)
leaves in .audio-cache/premix.wav, through the same limiter, into audio/score.wav.

  python audio/sfx.py        # after audio/remix.py and node scripts/build.mjs"""
import json, shutil, subprocess, wave
from pathlib import Path
import numpy as np

FILM = Path(__file__).resolve().parents[1]
FF = shutil.which('ffmpeg')
RATE = 48000

# kind: (seconds, where the peak falls, band centre in Hz at start / peak / end, band width in octaves)
SHAPES = {'swish': (.34, .35, (900, 3000, 4200), 1.2), 'whoosh': (.72, .55, (350, 1800, 700), 1.4),
          'whip': (.5, .45, (500, 5000, 1500), 1.0), 'rush': (1.5, .7, (250, 1500, 2400), 1.6),
          'riser': (.55, .92, (600, 3500, 7000), 1.0)}


def band_noise(n, rng, centre, width):
    """White noise through a band whose centre follows centre(p), p from 0 to 1 (short-time Fourier filtering)."""
    size, hop = 1024, 256
    x = rng.standard_normal(n + 2 * size)
    window = np.hanning(size)
    octave = np.log2(np.maximum(np.fft.rfftfreq(size, 1 / RATE), 1.0))
    out, weight = np.zeros(n + 2 * size), np.zeros(n + 2 * size)
    sigma = width / 2.355
    for start in range(0, n + size, hop):
        mask = np.exp(-.5 * ((octave - np.log2(centre(min(1.0, start / n)))) / sigma) ** 2)
        out[start:start + size] += np.fft.irfft(np.fft.rfft(x[start:start + size] * window) * mask) * window
        weight[start:start + size] += window ** 2
    return out[size // 2:size // 2 + n] / np.maximum(weight[size // 2:size // 2 + n], 1e-3)


def sound(kind, rng):
    """One sound as a (samples, 2) array peaking at 1."""
    if kind == 'boom':
        n = int(.9 * RATE); t = np.arange(n) / RATE
        pitch = 42 + 30 * np.exp(-t / .12)
        body = np.sin(2 * np.pi * np.cumsum(pitch) / RATE) * np.minimum(1, t / .005) * np.exp(-t / .25)
        click = band_noise(n, rng, lambda p: 180, 1.5) * np.exp(-t / .03) * .35
        mono = body + click
        return np.stack([mono, mono], 1) / np.abs(mono).max()
    seconds, peak, (f0, f1, f2), width = SHAPES[kind]
    n = int(seconds * RATE); p = np.arange(n) / n
    centre = lambda q: f0 * (f1 / f0) ** (q / peak) if q < peak else f1 * (f2 / f1) ** ((q - peak) / (1 - peak))
    common = band_noise(n, rng, centre, width)
    left, right = (.8 * common + .45 * band_noise(n, rng, centre, width) for _ in range(2))
    rise = np.sin(np.pi / 2 * np.minimum(1, p / peak)) ** 2
    fall = np.exp(-np.maximum(0, p - peak) / ((1 - peak) / 3.2))
    out = np.stack([left, right], 1) * (rise * fall * np.minimum(1, (1 - p) / .04))[:, None]
    return out / np.abs(out).max()


def main():
    timeline = json.loads((FILM / 'timeline.json').read_text(encoding='utf-8'))
    duration, events = timeline['durationSeconds'], timeline['sfx']
    bus = np.zeros((duration * RATE, 2))
    for i, e in enumerate(events):
        clip = sound(e['kind'], np.random.default_rng(1000 + i)) * 10 ** (e['gain'] / 20)
        pan = np.linspace(e['pan'][0], e['pan'][-1], len(clip))
        angle = (pan + 1) * np.pi / 4  # constant-power pan
        clip = clip * np.stack([np.cos(angle), np.sin(angle)], 1) * np.sqrt(2)
        at = round(e['t'] * RATE)
        if at < 0 or at >= len(bus):
            raise SystemExit(f'Sound {i} ({e["kind"]}) at {e["t"]} s is outside the film.')
        clip = clip[:len(bus) - at]
        bus[at:at + len(clip)] += clip
    cache = FILM / '.audio-cache'
    premix = cache / 'premix.wav'
    if not premix.exists():
        raise SystemExit('No .audio-cache/premix.wav: run audio/remix.py (or audio/score.py) first.')
    effects = cache / 'sfx.wav'
    with wave.open(str(effects), 'wb') as w:
        w.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        w.writeframes((np.clip(bus, -1, 1) * 32767).astype(np.int16).tobytes())
    subprocess.run([FF, '-y', '-i', str(premix), '-i', str(effects), '-filter_complex',
                    '[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89:level=false[mix]', '-map', '[mix]',
                    '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_s24le', str(FILM / 'audio/score.wav'), '-loglevel', 'error'],
                   check=True)
    print(f'{len(events)} sounds mixed into audio/score.wav ({duration} s)')


if __name__ == '__main__':
    main()
