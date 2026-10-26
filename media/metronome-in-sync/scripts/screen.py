"""Cut the real Wizard screen recordings (capture/) into the film's screen clips.

Each clip maps film time to capture time with anchors: a narration cue from assets/cues.json, or a moment of the
recorded run (a capture marker, or the start of a tool call as Wizard logged it). Between anchors time runs linearly,
so the run plays as a timelapse whose systems light up as the narrator names them. Frames are the capture's own
screencast frames, never redrawn; a cut inside a clip may cross-dissolve. Writes assets/screen/<clip>.mp4 and
assets/screen.json (clip windows, timelapse clocks and camera keyframes for compositions/runtime.js).

  python scripts/screen.py [--takes capture/takes] [--fps 30]"""
from __future__ import annotations
import argparse, bisect, json, re, shutil, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from PIL import Image

FILM = Path(__file__).resolve().parents[1]
PAGE = (1600, 900)  # the capture viewport in CSS pixels; camera keyframes use these coordinates
OUT_SIZE = (3200, 1800)  # the capture's device pixels (2x), kept so camera push-ins stay sharp

# Film-time references: a number, or "cue:<name>[+-seconds]". Capture references: "<marker>" or "tool:<label start>",
# each with an optional "+-seconds". A "cut" entry starts a new shot (optionally dissolving over "blend" seconds).
CLIPS = [
    {"id": "ask", "take": "quarter", "film": [33.2, 56.7], "shots": [[
        [33.2, "start+1.2"], [34.25, "start+3.25"], [35.45, "attached+0.2"], [35.75, "type_start"],
        [37.45, "type_end"], [38.2, "sent"],
        ["cue:sellOut-0.15", "tool:Read GSCM"], ["cue:share-0.15", "tool:Read ASAP"],
        ["cue:spend-0.15", "tool:Read NERP"], ["cue:targets-0.15", "tool:Queried attached file"],
        [49.25, "done"], [50.85, "settled"], [51.95, "top"], [56.7, "top+0.3"]]],
     "timelapse": {"from": 38.2, "to": 49.25, "clock": "sent"},
     "camera": [[33.2, 800, 450, 1], [34.0, 800, 450, 1], [34.9, 936, 780, 1.55], [37.6, 936, 780, 1.55],
                # The run: a slow push on the stage, a notch closer as each system is named; the frame's top edge stays
                # just below the conversation title (page y 45), so the push never clips the stage.
                [38.6, 936, 417, 1.45], ["cue:sellOut-0.2", 936, 415, 1.46], ["cue:sellOut+0.5", 936, 405, 1.5],
                ["cue:share-0.2", 936, 405, 1.5], ["cue:share+0.5", 936, 396, 1.54], ["cue:spend-0.2", 936, 396, 1.54],
                ["cue:spend+0.5", 936, 387, 1.58], ["cue:targets-0.2", 936, 387, 1.58], ["cue:targets+0.5", 936, 382, 1.6],
                [48.2, 936, 382, 1.6], [48.9, 936, 420, 1.12], [49.3, 936, 420, 1.12],
                [50.9, 936, 450, 1.05], [51.95, 936, 450, 1.1], ["cue:summary-0.1", 936, 300, 1.55],
                ["cue:numbers-0.25", 936, 300, 1.55], ["cue:numbers+0.2", 936, 725, 1.45],
                ["cue:charts-0.1", 936, 725, 1.45], ["cue:charts+0.4", 936, 470, 1.45], [55.7, 936, 470, 1.38],
                [56.45, 800, 450, 1]]},
    {"id": "stock", "take": "stock", "film": [55.8, 66.4], "shots": [[
        [55.8, "start+2.0"], [56.3, "type_start"], [57.9, "type_end"], [58.5, "sent"], [62.1, "done"],
        [63.3, "settled"], [64.2, "top"], [66.4, "top+0.1"]]],
     "timelapse": {"from": 58.5, "to": 62.1, "clock": "sent"},
     "camera": [[55.8, 800, 450, 1], [56.2, 936, 800, 1.55], [57.9, 936, 800, 1.55], [58.7, 936, 417, 1.45],
                [61.6, 936, 391, 1.56], [62.3, 936, 420, 1.12], [63.3, 936, 420, 1.1], [64.2, 936, 370, 1.46],
                [65.4, 936, 400, 1.52], [66.1, 800, 450, 1.05]]},
    {"id": "check", "take": "quarter", "film": [65.7, 75.9], "shots": [
        [[65.7, "bottom+0.6"], [66.55, "check_click+0.04"], [67.6, "check_done"], [68.3, "check_panel"],
         [68.9, "check_panel+0.6"]],
        {"cut": True, "blend": 0.3, "anchors": [[68.9, "citation_click-0.3"], [69.6, "evidence"],
                                                [71.0, "evidence+1.3"], [75.9, "evidence+2.0"]]}],
     "timelapse": {"from": 66.55, "to": 67.6, "clock": "check_click"},
     "camera": [[65.7, 700, 640, 1.35], [66.5, 700, 690, 1.45], [66.95, 936, 417, 1.45], [67.55, 936, 400, 1.52],
                [68.3, 936, 560, 1.4], [68.85, 936, 560, 1.4], [68.95, 1180, 420, 1.15], [69.6, 1240, 560, 1.3],
                [71.2, 1240, 600, 1.38], [75.9, 1240, 600, 1.45]]},
]


def load_take(root: Path, take: str):
    frames = json.loads((root / take / 'frames.json').read_text())
    meta = json.loads((root / take / 'markers.json').read_text())
    t0 = meta['markers']['start']['t']
    events = {k: v['t'] - t0 for k, v in meta['markers'].items()}
    tools = [(e['label'] or '', datetime.fromisoformat(e['ts']).timestamp() - t0)
             for r in meta['runs'] for e in r['events'] if e['type'] == 'tool_started']
    times = [f['t'] - t0 for f in frames['frames']]
    files = [root / take / 'frames' / f['file'] for f in frames['frames']]
    return events, tools, times, files


def offset(ref: str):
    match = re.fullmatch(r'(.*?)([+-]\d+(?:\.\d+)?)?', ref)
    return match.group(1), float(match.group(2) or 0)


def film_time(ref, cues):
    if isinstance(ref, (int, float)):
        return float(ref)
    name, delta = offset(ref)
    return cues[name.removeprefix('cue:')] + delta


def source_time(ref: str, events, tools):
    name, delta = offset(ref)
    if name.startswith('tool:'):
        hit = next((t for label, t in tools if label.startswith(name[5:])), None)
        if hit is None:
            raise SystemExit(f'No tool call starting "{name[5:]}" in the take.')
        return hit + delta
    return events[name] + delta


def mapper(anchors):
    xs, ys = [a[0] for a in anchors], [a[1] for a in anchors]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise SystemExit(f'Film anchors must increase: {xs}')
    def at(t):
        if t <= xs[0]: return ys[0] + (t - xs[0])  # before or after a shot, time runs at speed 1
        if t >= xs[-1]: return ys[-1] + (t - xs[-1])
        i = bisect.bisect_right(xs, t) - 1
        return ys[i] + (ys[i + 1] - ys[i]) * (t - xs[i]) / (xs[i + 1] - xs[i])
    return at, xs[0], xs[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--takes', default=str(FILM / 'capture' / 'takes'))
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--crf', type=int, default=20)
    ap.add_argument('--only')
    ap.add_argument('--camera-only', action='store_true', help='rewrite assets/screen.json without re-encoding the clips')
    args = ap.parse_args()
    cues = json.loads((FILM / 'assets/cues.json').read_text())['cues']
    takes, out = Path(args.takes), FILM / 'assets' / 'screen'
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = FILM / 'assets/screen.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update({'page': PAGE, 'clips': manifest.get('clips', {})})
    for clip in CLIPS:
        if args.only and clip['id'] != args.only:
            continue
        events, tools, times, files = load_take(takes, clip['take'])
        shots = []
        for shot in clip['shots']:
            spec = shot if isinstance(shot, dict) else {'anchors': shot}
            anchors = [(film_time(f, cues), source_time(s, events, tools)) for f, s in spec['anchors']]
            at, start, end = mapper(anchors)
            shots.append({'at': at, 'start': start, 'end': end, 'blend': spec.get('blend', 0), 'anchors': anchors})
        begin, finish = clip['film']
        n = round((finish - begin) * args.fps)
        frame_at = lambda s: files[max(0, bisect.bisect_right(times, s) - 1)]
        work = Path(tempfile.mkdtemp(prefix=f'screen-{clip["id"]}-'))
        used = []
        for k in (range(0) if args.camera_only else range(n)):
            t = begin + (k + .5) / args.fps
            i = max([j for j, s in enumerate(shots) if t >= s['start'] - s['blend'] / 2] or [0])
            shot, link = shots[i], work / f'{k:05d}.jpg'
            fade = shot['blend'] and i > 0 and t < shot['start'] + shot['blend'] / 2
            if fade:
                a = (t - (shot['start'] - shot['blend'] / 2)) / shot['blend']
                prev = shots[i - 1]
                Image.blend(Image.open(frame_at(prev['at'](min(t, prev['end'])))).convert('RGB'),
                            Image.open(frame_at(shot['at'](t))).convert('RGB'), a * a * (3 - 2 * a)).save(link, quality=95)
            else:
                link.symlink_to(frame_at(shot['at'](t)))
            used.append(round(shot['at'](t), 3))
        target = out / f'{clip["id"]}.mp4'
        if not args.camera_only:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(args.fps), '-i', str(work / '%05d.jpg'),
                            '-vf', f'scale={OUT_SIZE[0]}:{OUT_SIZE[1]}:flags=lanczos,format=yuv420p', '-c:v', 'libx264',
                            '-preset', 'slow', '-crf', str(args.crf), '-g', str(args.fps), '-movflags', '+faststart',
                            '-an', str(target)], check=True)
        shutil.rmtree(work)
        tl = clip.get('timelapse')
        manifest['clips'][clip['id']] = {
            'file': f'assets/screen/{clip["id"]}.mp4', 'start': begin, 'end': finish, 'take': clip['take'],
            'anchors': [[round(f, 3), round(s, 3)] for shot in shots for f, s in shot['anchors']],
            'timelapse': tl and {'from': tl['from'], 'to': tl['to'], 'clockFrom': round(source_time(tl['clock'], events, tools), 3),
                                 'sourceFrom': round(shots[0]['at'](tl['from']), 3), 'sourceTo': round(shots[0]['at'](tl['to']), 3)},
            'camera': [[round(film_time(c[0], cues), 3), *c[1:]] for c in clip['camera']],
        }
        if used:
            print(f'{clip["id"]}: {n} frames, {target.stat().st_size / 1e6:.1f} MB, source {used[0]:.1f}–{used[-1]:.1f} s')
    manifest_path.write_text(json.dumps(manifest, indent=1) + '\n')


if __name__ == '__main__':
    main()
