"""Deterministic, fictional-data film score. NumPy is the only Python dependency."""
import json
import math
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = json.loads((ROOT / "timeline.json").read_text(encoding="utf-8"))
RATE = 48000
BEAT_SECONDS = 60 / TIMELINE["bpm"]
DURATION = TIMELINE["durationBeats"] * BEAT_SECONDS
N = round(DURATION * RATE)
RNG = np.random.default_rng(TIMELINE["seed"])
STEMS = {name: np.zeros((N, 2), dtype=np.float32) for name in ("ticks", "pulse", "pad", "fx")}
EVENT = {e["id"]: e for e in TIMELINE["events"]}


def beat_time(value):
    return value * BEAT_SECONDS


def add(name, start, sound, pan=0.0, gain=1.0):
    """Place a mono event into a stereo stem with constant-power panning."""
    first = max(0, round(start * RATE))
    if first >= N:
        return
    last = min(N, first + len(sound))
    if last <= first:
        return
    angle = (np.clip(pan, -1, 1) + 1) * math.pi / 4
    STEMS[name][first:last, 0] += sound[:last-first] * (math.cos(angle) * gain)
    STEMS[name][first:last, 1] += sound[:last-first] * (math.sin(angle) * gain)


def envelope(length, attack, release):
    v = np.ones(length, np.float32)
    a = min(length, round(attack * RATE))
    r = min(length, round(release * RATE))
    if a:
        v[:a] *= np.linspace(0, 1, a, dtype=np.float32)
    if r:
        v[-r:] *= np.linspace(1, 0, r, dtype=np.float32)
    return v


def wooden_tick(strength=1.0):
    length = round(.105 * RATE)
    x = np.arange(length, dtype=np.float32) / RATE
    noise = RNG.standard_normal(length).astype(np.float32)
    # A first difference gives the short click a crisp, band-focused attack.
    click = np.empty_like(noise)
    click[0] = noise[0]
    click[1:] = noise[1:] - .82 * noise[:-1]
    click *= np.exp(-x * 110)
    resonant = np.sin(2 * np.pi * 2200 * x) * np.exp(-x * 47)
    return (click * .027 + resonant * .075).astype(np.float32) * strength


def ping(freq=1174, decay=.34, gain=.07):
    x = np.arange(round(decay * RATE), dtype=np.float32) / RATE
    return ((np.sin(2 * np.pi * freq * x) + .22 * np.sin(2 * np.pi * freq * 2.01 * x))
            * np.exp(-x * 13) * gain).astype(np.float32)


def felt_note(freq, seconds=1.55, gain=.038):
    """A soft, short keyboard tone with no low-frequency impact."""
    x = np.arange(round(seconds * RATE), dtype=np.float32) / RATE
    attack = 1 - np.exp(-x * 35)
    decay = np.exp(-x * 2.9)
    tone = (np.sin(2 * np.pi * freq * x)
            + .14 * np.sin(2 * np.pi * freq * 2 * x)
            + .035 * np.sin(2 * np.pi * freq * 3 * x))
    return (tone * attack * decay * gain).astype(np.float32)


def boom(gain=.22, seconds=1.7):
    x = np.arange(round(seconds * RATE), dtype=np.float32) / RATE
    sweep = 47 + 80 * np.exp(-x * 13)
    phase = 2 * np.pi * np.cumsum(sweep) / RATE
    transient = RNG.standard_normal(len(x)).astype(np.float32) * np.exp(-x * 90) * .055
    return (np.sin(phase) * np.exp(-x * 3.2) * gain + transient).astype(np.float32)


def airy_whoosh():
    seconds = .44
    x = np.arange(round(seconds * RATE), dtype=np.float32) / RATE
    noise = RNG.standard_normal(len(x)).astype(np.float32)
    filtered = np.convolve(noise, np.ones(28, np.float32) / 28, mode="same")
    return filtered.astype(np.float32) * np.sin(np.pi * x / seconds).astype(np.float32) * .06


def add_pad(start, end, frequencies, gain, attack=1.8, release=1.7):
    first, last = round(start * RATE), min(N, round(end * RATE))
    if last <= first:
        return
    x = np.arange(last-first, dtype=np.float32) / RATE
    env = envelope(len(x), attack, release)
    block = np.zeros((len(x), 2), np.float32)
    for j, freq in enumerate(frequencies):
        detune = 1.0 + (j-1) * .003
        left = np.sin(2*np.pi*freq*x) + .18*np.sin(2*np.pi*freq*2*x)
        right = np.sin(2*np.pi*freq*detune*x + j*.3) + .18*np.sin(2*np.pi*freq*2*detune*x)
        block[:, 0] += left.astype(np.float32)
        block[:, 1] += right.astype(np.float32)
    # Slow amplitude motion serves as a warm, restrained filter-like opening.
    motion = (.75 + .25*np.sin(2*np.pi*.085*x - math.pi/2)).astype(np.float32)
    block *= (env * motion * gain / len(frequencies))[:, None]
    STEMS["pad"][first:last] += block


def add_pulse(start, end, strength=.11, interval=.5):
    for at in np.arange(start, end, interval):
        x = np.arange(round(.36 * RATE), dtype=np.float32) / RATE
        note = (np.sin(2*np.pi*53*x) * np.exp(-x*10) * strength).astype(np.float32)
        add("pulse", float(at), note)


def reverb_taps(signal, taps):
    """A reproducible sparse synthesized impulse response, applied as delay taps."""
    dry = signal.copy()
    for delay, gain, swap in taps:
        offset = round(delay * RATE)
        if swap:
            signal[offset:] += dry[:-offset, ::-1] * gain
        else:
            signal[offset:] += dry[:-offset] * gain


def write24(path, samples):
    # Explicit little-endian 24-bit PCM; float arrays never leave this script.
    q = np.clip(np.rint(np.clip(samples, -1, 1) * 8388607), -8388608, 8388607).astype("<i4")
    b = q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(RATE)
        w.writeframes(b)


def master_mix():
    """Use the pinned local FFmpeg binary for two-pass delivery-safe mastering."""
    ffmpeg_path = os.environ.get("FILM_FFMPEG_PATH") or shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise FileNotFoundError("FFmpeg is required to master the score; install it or set FILM_FFMPEG_PATH")
    ffmpeg = Path(ffmpeg_path)
    source = ROOT / "audio/score-pre.wav"
    target = ROOT / "audio/score-master.wav"
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"FFmpeg executable is missing: {ffmpeg}")

    def run(args):
        result = subprocess.run([str(ffmpeg), "-hide_banner", *args], stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, check=True)
        blocks = re.findall(r'\{\s*"input_i"\s*:[\s\S]*?\}', result.stderr)
        if not blocks:
            raise RuntimeError("FFmpeg did not report loudness measurements")
        return json.loads(blocks[-1])

    first = run(["-i",str(source),"-af","loudnorm=I=-20:TP=-7.5:LRA=11:print_format=json","-f","null","NUL"])
    filt = ("loudnorm=I=-20:TP=-7.5:LRA=11:"
            f"measured_I={first['input_i']}:measured_TP={first['input_tp']}:"
            f"measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}:"
            f"offset={first['target_offset']}:linear=false:print_format=json")
    run(["-y","-i",str(source),"-af",filt,"-ar","48000","-c:a","pcm_s24le",str(target)])
    target.replace(ROOT / "audio/score.wav")
    return run(["-i",str(ROOT / "audio/score.wav"),"-af",
                "loudnorm=I=-20:TP=-6:LRA=11:print_format=json","-f","null","NUL"])


def main():
    # Space for a live presenter: a few brand ticks, quiet notes, and action cues.
    for at, strength in [(0, .7), (2, .48), (4, .48), (6, .48)]:
        add("ticks", at, wooden_tick(strength), gain=.43)
    for i, at in enumerate([9, 9.75, 10.5, 12, 14, 16]):
        add("fx", at, ping([740, 880, 988, 1174][i % 4], .22, .017),
            pan=[-.5, .35, -.2, .45][i % 4])
    for i in range(1, 5):
        at = beat_time(EVENT[f"system-ping-{i}"]["startBeat"])
        add("fx", at, ping([988, 1174, 1318, 1568][i-1], .28, .024),
            pan=[-.55, -.18, .22, .55][i-1])
    for beat in [31, 33, 35, 37, 39]:
        add("fx", beat_time(beat), wooden_tick(.2), gain=.18)

    # The pivot has an intentional half-second of absolute silence.
    add("ticks", 20.5, wooden_tick(.75), gain=.46)
    for at, notes in [
        (21, [261.63, 392.00]), (24, [293.66]), (28, [329.63]),
        (32, [392.00]), (36, [261.63, 329.63]), (40, [392.00]),
        (44, [329.63]), (46, [293.66, 440.00]),
        (54, [329.63]), (59, [392.00]), (64, [261.63, 329.63]),
        (69, [392.00]), (74, [293.66]), (79, [523.25])]:
        for j, note in enumerate(notes):
            add("pulse", at + j*.14, felt_note(note, gain=.032 if at != 79 else .023),
                pan=(-.18 if j == 0 else .18))

    add_pad(21, 24, [261.63, 329.63, 392.00], .012, attack=.8, release=1.3)
    add_pad(35.8, 45.5, [261.63, 329.63, 392.00], .008, attack=1.5, release=2)
    add_pad(53.5, 59.5, [293.66, 392.00, 440.00], .008, attack=1.2, release=1.8)
    add_pad(63.8, 73.5, [261.63, 329.63, 392.00], .010, attack=1.5, release=2)
    add_pad(81, 83.45, [261.63, 329.63, 392.00], .005, attack=.6, release=1.4)

    for i in range(1, 5):
        at = beat_time(EVENT[f"record-click-{i}"]["startBeat"])
        add("fx", at, wooden_tick(.32), gain=.28)
    pipeline_start = EVENT["record-parallel"]["startBeat"]
    for i in range(5):
        add("fx", beat_time(pipeline_start + i*2),
            felt_note(523.25 + i*73.4, .65, .015), pan=(i-2)*.14)
    for at in [38, 40, 42, 44]:
        add("fx", at, ping(740, .19, .011), pan=math.sin(at)*.3)
    for at in [47, 49, 51, 53]:
        add("fx", at, wooden_tick(.18), gain=.22)
    for at, note in [(51.5, 659.25), (52.5, 783.99), (53.5, 880.00)]:
        add("fx", at, felt_note(note, .75, .015), pan=.18)
    add("fx", beat_time(EVENT["email-chime"]["startBeat"]),
        felt_note(784.00, 1.0, .022))
    add("fx", beat_time(EVENT["audit-tension"]["startBeat"]),
        felt_note(392.00, .95, .018))
    for b in EVENT["s8-thin-ticks"]["beats"]:
        add("ticks", beat_time(b), wooden_tick(.34), gain=.24)
    add("ticks", beat_time(EVENT["final-tick"]["startBeat"]),
        wooden_tick(.7), gain=.42)

    # Two low-level reflections keep the interface cues natural without a cinematic tail.
    reverb_taps(STEMS["fx"], [(.09, .08, True), (.17, .035, False)])
    for a, z in [(20, 20.5), (78.5, 79), (83.5, 84)]:
        start, end = round(a*RATE), round(z*RATE)
        for stem in STEMS.values():
            stem[start:end] = 0
    fade_start, fade_end = round(74*RATE), round(76*RATE)
    for name in ("pulse", "pad", "fx"):
        STEMS[name][fade_start:fade_end] *= np.linspace(
            1, 0, fade_end-fade_start, dtype=np.float32)[:, None]
        STEMS[name][fade_end:round(79*RATE)] = 0
    mix = sum(STEMS.values())
    peak = float(np.max(np.abs(mix)))
    gain = min(1.0, .82/max(peak, 1e-6))
    for name, stem in STEMS.items():
        write24(ROOT / "audio" / f"{name}.wav", stem * gain)
    write24(ROOT / "audio/score-pre.wav", mix * gain)
    measured = master_mix()
    print(json.dumps({"durationSeconds":DURATION,"sampleRate":RATE,"bitDepth":24,
                      "stems":list(STEMS),"samplePeakDbfs":round(20*math.log10(peak*gain),2),
                      "preMasterGain":round(gain,5),"masterWavLufs":measured["input_i"],
                      "masterWavTruePeakDbtp":measured["input_tp"]}, indent=2))


if __name__ == "__main__":
    main()
