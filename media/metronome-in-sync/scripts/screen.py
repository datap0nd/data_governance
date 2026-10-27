"""Cut the real Wizard screen recordings (capture/) into the film's screen clips.

A clip is a path through one recorded take. It starts at a film time and a capture moment; each step then moves to a
later capture moment at real speed (1), sped up (a speed above 1: the timelapse, used only while Wizard is working),
or held over a given film duration. A cut jumps to another capture moment and may cross-dissolve. Capture moments are
markers the recorder logged, the start of a tool call as Wizard logged it, or the moment a run's systems stage scrolls
away for its answer, so the timelapse follows the real run. Frames are the capture's own screencast frames (1600 x 900),
never redrawn; while Wizard works they are only moved up or down so its stage holds still (the stage lock below).
Writes assets/screen/<clip>.mp4 and assets/screen.json (clip windows, timelapse ranges and, for every frame, where
compositions/runtime.js places the window: its top-left corner in the frame and its scale).

  python scripts/screen.py [--takes capture/takes] [--report] [--camera-only]"""
from __future__ import annotations
import argparse, bisect, json, re, shutil, subprocess, tempfile
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image

FILM = Path(__file__).resolve().parents[1]
PAGE = (1600, 900)  # the capture viewport in CSS pixels, which is also the screencast frame size; the camera uses these coordinates
OUT_SIZE = (3200, 1800)  # the clips are scaled up 2x so the camera's push-ins are resampled once, not twice
LAPSE = 2.25  # question 1: Wizard's reads are 4.3 s apart, so at 2.25x each system lights up as the narrator names it
STOCK_LAPSE = 4  # question 2: its run fits under one line of narration
CHECK_LAPSE = 2  # the check, slow enough to follow it
# While a stage lock is on, the picture may show page rows from just below the conversation title to just above the
# composer: the lock moves the conversation, not those fixed bars, so they must stay out of frame.
LOCK_ROWS = (46, 790)

# Capture references: "<marker>", "tool:<label start>" or "stage_end" / "check_stage_end", with an optional "+-seconds".
# Film references: a number, "cue:<name>[+-s]", or "src:<capture reference>[@+-s]" (the film time this clip reaches
# that capture moment). Steps: [capture, speed], [capture, "until", film reference] (reach it then, at whatever speed
# that takes), or a cut {"cut": capture, "blend": seconds}. A clip may start "after:<clip>-s", overlapping the previous
# one for the hand-off. Timelapses: [from, to, clock] capture references for each stretch where Wizard works; the
# pill's clock counts from clock. Locks: [from, to] capture references; each must end at a cut.
# Camera: [film reference, fx, fy, z] keyframes: the page point (fx, fy) at the frame centre at zoom z. Between keyframes
# the camera follows a monotone cubic, so it eases into and out of holds (repeated values) and passes through the
# others without stopping; it never overshoots. Keep moves few and slow: the page moves enough on its own.
CLIPS = [
    {"id": "ask", "take": "quarter", "start": [33.2, "start+0.4"],
     "path": [["type_start", "until", "cue:askQuarter-0.25"],   # the home screen; the pointer goes to the question box
              ["sent+2.6", 1],                                  # type, attach the file, send; Wizard appears and plans
              ["stage_end-0.1", LAPSE],                         # timelapse: the reads, the sums, the chart
              {"cut": "top", "blend": 0.6},                     # the answer, from its top
              ["top+1.6", "until", "cue:charts+1.6"]],
     "timelapse": [["sent+2.6", "stage_end-0.1", "sent"]],
     "lock": [["sent+0.9", "stage_end-0.1"]],
     "camera": [["src:start+0.4", 800, 450, 1], ["src:type_start@-1.0", 800, 450, 1.0],
                ["src:type_start@+0.2", 936, 520, 1.5], ["src:attached@+0.3", 936, 522, 1.52],   # the question box
                ["src:sent+0.9", 936, 455, 1.62], ["src:stage_end-0.1@-0.3", 936, 462, 1.7],    # the stage, held
                ["src:top@+1.2", 920, 400, 1.42], ["cue:summary-0.6", 936, 350, 1.55],         # the summary
                ["cue:charts-0.5", 936, 470, 1.5], ["cue:charts+1.6", 936, 474, 1.52]]},       # down to the chart
    {"id": "stock", "take": "stock", "start": ["after:ask-0.8", "start+1.2"],
     "path": [["type_start", "until", "cue:askStock-0.25"],
              ["sent+2.6", 1],
              ["stage_end-0.1", STOCK_LAPSE],
              {"cut": "top", "blend": 0.6},
              ["top+2.3", "until", "cue:clickCheck-3.6"],               # read the answer
              ["check_click+3.0", 1],                                    # scroll down, click Check my data; it starts
              ["check_stage_end-0.1", CHECK_LAPSE],                      # timelapse
              {"cut": "check_panel", "blend": 0.6},                      # the six MATCH rows
              ["check_panel+2.1", "until", "cue:checked+2.9"],
              ["evidence+0.4", 1],                                       # back to the figure, open its sources
              ["evidence+1.6", "until", "cue:stop-0.31"]],
     "timelapse": [["sent+2.6", "stage_end-0.1", "sent"], ["check_click+3.0", "check_stage_end-0.1", "check_click"]],
     "lock": [["sent+0.9", "stage_end-0.1"], ["check_click+1.2", "check_stage_end-0.1"]],
     "camera": [["src:start+1.2", 880, 450, 1.35], ["src:type_start@-0.6", 936, 520, 1.5],
                ["src:sent@+0.3", 936, 522, 1.52],
                ["src:sent+0.9", 936, 412, 1.62], ["src:stage_end-0.1@-0.3", 936, 410, 1.7],    # the stage, held
                ["src:top@+1.2", 920, 400, 1.42], ["src:top+2.3", 936, 350, 1.55],             # the answer
                ["src:check_click@-0.9", 840, 470, 1.3],                                        # the Check my data button
                ["src:check_click+1.2", 936, 412, 1.66], ["src:check_stage_end-0.1@-0.3", 936, 410, 1.7],
                ["src:check_panel@+1.2", 920, 500, 1.45], ["src:check_panel+2.1", 920, 496, 1.48],  # six MATCH rows
                ["src:citation_click@-0.6", 840, 440, 1.3], ["src:evidence", 850, 470, 1.32],  # the Sources drawer
                ["cue:stop-0.31", 850, 470, 1.36]]},
]


def load_take(root: Path, take: str):
    folder = root / take
    frames = json.loads((folder / 'frames.json').read_text()) if (folder / 'frames.json').exists() else None
    meta = json.loads((folder / 'markers.json').read_text())
    t0 = meta['markers']['start']['t']
    events = {k: v['t'] - t0 for k, v in meta['markers'].items()}
    tools = [(e['label'] or '', datetime.fromisoformat(e['ts']).timestamp() - t0)
             for r in meta['runs'] for e in r['events'] if e['type'] == 'tool_started']
    geometry = None
    if (folder / 'geometry.json').exists():
        geometry = [[s[0] - t0, *s[1:]] for s in json.loads((folder / 'geometry.json').read_text())['samples']]
        # When a run's answer starts, the conversation scrolls its stage away: the first big jump up after it began.
        for name, after in (('stage_end', 'sent'), ('check_stage_end', 'check_click')):
            later = [(a, b) for a, b in zip(geometry, geometry[1:]) if a[0] > events[after] + 1]
            events[name] = next(b[0] for a, b in later if b[1] - a[1] < -120)
    times = [f['t'] - t0 for f in frames['frames']] if frames else []
    files = [folder / 'frames' / f['file'] for f in frames['frames']] if frames else []
    return events, tools, times, files, geometry


def offset(ref: str):
    match = re.fullmatch(r'(.*?)([+-]\d+(?:\.\d+)?)?', ref)
    return match.group(1), float(match.group(2) or 0)


def source_time(ref: str, events, tools):
    name, delta = offset(ref)
    if name.startswith('tool:'):
        hit = next((t for label, t in tools if label.startswith(name[5:])), None)
        if hit is None:
            raise SystemExit(f'No tool call starting "{name[5:]}" in the take.')
        return hit + delta
    return events[name] + delta


class Path_:
    """Film ↔ capture time for one clip: shots of linear segments, joined by cuts."""
    def __init__(self, clip, start, events, tools, cues, fps):
        src = lambda ref: source_time(ref, events, tools)
        t, s = start, src(clip['start'][1])
        self.shots = [{'segments': [], 'blend': 0}]
        for step in clip['path']:
            if isinstance(step, dict):
                s = src(step['cut'])
                self.shots.append({'segments': [], 'blend': step.get('blend', 0)})
                continue
            target = src(step[0])
            if step[1] == 'until':
                try:
                    length = film_ref(step[2], cues, None) - t
                except KeyError:  # no voice timing yet (a --report before the first remix): hold briefly
                    length = 3.0
            else:
                length = (target - s) / step[1]
            if length <= 0 or target < s:
                raise SystemExit(f'{clip["id"]}: step {step} goes backwards (film {t:.2f}, capture {s:.2f}).')
            # Whole frames, so a 1x step stays exactly 1x and steps never straddle a frame boundary.
            length = round(length * fps) / fps
            self.shots[-1]['segments'].append((t, t + length, s, target))
            t, s = t + length, target
        self.start, self.end = start, t

    def shot_at(self, t):
        index = 0
        for i, shot in enumerate(self.shots):
            if shot['segments'] and t >= shot['segments'][0][0] - shot['blend'] / 2:
                index = i
        return index

    def source(self, t, index=None):
        segments = self.shots[self.shot_at(t) if index is None else index]['segments']
        f0, f1, s0, s1 = next((g for g in segments if g[0] <= t < g[1]), segments[0] if t < segments[0][0] else segments[-1])
        return s0 + (s1 - s0) * (t - f0) / (f1 - f0)  # extrapolates past a shot's ends at its edge speed

    def film(self, s):
        """The film time this clip first shows capture moment s."""
        return self.locate(s)[0]

    def locate(self, s):
        """(film time, shot) where this clip first shows capture moment s."""
        for shot in self.shots:
            for f0, f1, s0, s1 in shot['segments']:
                if s0 <= s <= s1:
                    return f0 + (f1 - f0) * (s - s0) / (s1 - s0), shot
        raise SystemExit(f'Capture time {s:.2f} is not shown in this clip.')


def film_ref(ref, cues, path, events=None, tools=None):
    if isinstance(ref, (int, float)):
        return float(ref)
    if ref.startswith('src:'):
        capture, _, shift = ref[4:].partition('@')
        return path.film(source_time(capture, events, tools)) + float(shift or 0)
    name, delta = offset(ref)
    return cues[name.removeprefix('cue:')] + delta


def track_stage(times, files, geometry, s0, s1):
    """The page row of the newest systems stage's top edge in every capture frame from s0 to s1, by frame index.

    Wizard's conversation keeps its newest line in view, so each time a tool starts or ends and the stage's status line
    appears or goes, everything scrolls about 23 px; at timelapse speed that reads as jitter. The browser's own log
    (geometry.json) runs a frame or two ahead of the screencast, so it only seeds the first frame; after that the stage
    is followed frame to frame by matching its picture, so every value belongs to exactly the frame it describes."""
    first = max(0, bisect.bisect_right(times, s0) - 1)
    last = max(0, bisect.bisect_right(times, s1) - 1)
    log = [g[0] for g in geometry]
    top = geometry[max(0, bisect.bisect_right(log, times[first] - 0.15) - 1)][1]
    load = lambda i: np.asarray(Image.open(files[i]).convert('L'), np.float32)[:, 480:1390:2]
    tops, prev = {first: top}, load(first)
    for i in range(first + 1, last + 1):
        cur = load(i)
        r0, r1 = int(max(top, 50)), int(min(top + 300, PAGE[1] - 60))
        ref = prev[r0:r1]
        score = lambda d: np.abs(cur[r0 + d:r1 + d] - ref).mean() if r0 + d >= 0 and r1 + d <= PAGE[1] else np.inf
        top += min(range(-80, 81), key=score)
        tops[i], prev = top, cur
    # Every tracked position must be one the browser logged around that frame.
    for i, value in tops.items():
        near = [g[1] for g in geometry[bisect.bisect_left(log, times[i] - 0.4):bisect.bisect_right(log, times[i] + 0.1)]]
        if near and min(abs(v - value) for v in near) > 1.5:
            raise SystemExit(f'Stage tracking disagrees with the browser log at capture {times[i]:.2f}: '
                             f'{value:.1f}, where the log has {sorted(set(round(v, 1) for v in near))}.')
    return tops


def pchip(keys, t):
    """Monotone cubic (Fritsch-Carlson) through keyframes [(time, value)] at time t; flat outside them."""
    xs, ys = [k[0] for k in keys], [k[1] for k in keys]
    if t <= xs[0]:
        return ys[0]
    if t >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, t) - 1
    h = [xs[k + 1] - xs[k] for k in range(len(xs) - 1)]
    d = [(ys[k + 1] - ys[k]) / h[k] for k in range(len(h))]
    def slope(k):
        if k == 0 or k == len(xs) - 1:
            return 0.0  # every move starts and ends at rest
        if d[k - 1] * d[k] <= 0:
            return 0.0
        w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
        return (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    p = (t - xs[i]) / h[i]
    return ((2 * p ** 3 - 3 * p ** 2 + 1) * ys[i] + (p ** 3 - 2 * p ** 2 + p) * h[i] * slope(i) +
            (-2 * p ** 3 + 3 * p ** 2) * ys[i + 1] + (p ** 3 - p ** 2) * h[i] * slope(i + 1))


def placement(fx, fy, z):
    """Where compositions/runtime.js puts the window for camera (fx, fy, z): its top-left corner in the 1920 x 1080
    frame, before any clamping. Near z = 1 the window eases back to the centre."""
    w = min(1, max(0, (z - 1) / .3))
    w = w * w * (3 - 2 * w)
    return 960 - 800 * z + (800 * z - fx * z) * w, 540 - 450 * z + (450 * z - fy * z) * w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--takes', default=str(FILM / 'capture' / 'takes'))
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--crf', type=int, default=20)
    ap.add_argument('--only')
    ap.add_argument('--camera-only', action='store_true', help='rewrite assets/screen.json without re-encoding the clips')
    ap.add_argument('--report', action='store_true', help='print when each recorded moment appears in the film; write nothing')
    args = ap.parse_args()
    cues_file = FILM / 'assets/cues.json'
    cues = json.loads(cues_file.read_text())['cues'] if cues_file.exists() else {}
    takes, out = Path(args.takes), FILM / 'assets' / 'screen'
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = FILM / 'assets/screen.json'
    manifest = {'page': PAGE, 'clips': {}}
    if args.only and manifest_path.exists():  # re-cut one clip; keep the others as they are
        manifest['clips'] = json.loads(manifest_path.read_text())['clips']
    ends = {}
    for clip in CLIPS:
        events, tools, times, files, geometry = load_take(takes, clip['take'])
        src = lambda ref: source_time(ref, events, tools)
        begin = clip['start'][0]
        if isinstance(begin, str):
            name, delta = offset(begin.removeprefix('after:'))
            begin = ends[name] + delta
        path = Path_(clip, begin, events, tools, cues, args.fps)
        ends[clip['id']] = path.end
        n = round((path.end - path.start) * args.fps)
        frame_at = lambda s: max(0, bisect.bisect_right(times, s) - 1)
        film_times = [begin + (k + .5) / args.fps for k in range(n)]

        # Stage locks: each frame inside one is moved by how far the stage has moved since the lock began.
        locks = []
        for a, b in clip.get('lock', []):
            s0, s1 = src(a), src(b)
            _, shot = path.locate(s1)
            if shot['segments'][-1][3] != s1:
                raise SystemExit(f'{clip["id"]}: the lock {a}..{b} must end where its shot ends, at a cut.')
            cache = takes / clip['take'] / f'stage-{s0:.3f}-{s1:.3f}.json'
            if cache.exists():
                tops = {int(k): v for k, v in json.loads(cache.read_text()).items()}
            else:
                tops = track_stage(times, files, geometry, s0, s1) if times else {}
                if tops:
                    cache.write_text(json.dumps(tops))
            locks.append((s0, s1, tops, tops.get(frame_at(s0))))
        def shift(s):
            for s0, s1, tops, top0 in locks:
                if s0 <= s <= s1 + 1e-6:
                    return round(tops[frame_at(s)] - top0)
            return 0

        # The camera for every frame of the clip, from its keyframes.
        keys = [(film_ref(c[0], cues, path, events, tools), *c[1:]) for c in clip['camera']]
        if any(b[0] <= a[0] for a, b in zip(keys, keys[1:])):
            raise SystemExit(f'{clip["id"]}: camera keyframes are out of order: {[round(k[0], 2) for k in keys]}')
        # Keyframes become window placements (x, y, z) and those are interpolated, so a move between two framings
        # that each keep the window's edge out of the frame keeps it out all the way.
        places = [(k[0], *placement(*k[1:]), k[3]) for k in keys]
        camera = [[round(pchip([(c[0], c[d]) for c in places], begin + k / args.fps), 2) for d in (1, 2, 3)] for k in range(n)]
        problems = []
        for k, (x, y, z) in enumerate(camera):
            t = begin + k / args.fps
            # Between two close framings (z >= 1.3) the window's edge must never show. A move from or to the floating
            # window (z near 1) shows it by design while the window grows to fill the frame or shrinks back.
            j = max(0, min(len(places) - 2, bisect.bisect_right([c[0] for c in places], t) - 1))
            close = places[j][3] >= 1.3 and places[j + 1][3] >= 1.3
            if close and not (1920 - 1600 * z - .5 <= x <= .5 and 1080 - 900 * z - .5 <= y <= .5):
                problems.append((t, f'the window ({x}, {y}, {z}) shows its edge'))
            if locks and times:
                i = path.shot_at(t)
                d = shift(path.source(t, i)) if any(s0 <= path.source(t, i) <= s1 for s0, s1, *_ in locks) else None
                top, bottom = -y / z, (1080 - y) / z  # the page rows in frame
                if d is not None and not (LOCK_ROWS[0] - d <= top and bottom <= LOCK_ROWS[1] - d):
                    problems.append((t, f'rows {top:.0f}-{bottom:.0f} show a fixed bar under a stage shift of {d}'))
        if problems:  # the first frame of each run of bad frames
            runs = [f'film {t:.2f}: {what}' for k, (t, what) in enumerate(problems) if k == 0 or t - problems[k - 1][0] > 1.5 / args.fps]
            raise SystemExit(f'{clip["id"]}: {len(problems)} frames go wrong, from\n  ' + '\n  '.join(runs[:20]))

        if args.report:
            print(f'{clip["id"]}: film {path.start:.2f}–{path.end:.2f}')
            moments = sorted([(v, k) for k, v in events.items()] + [(t, 'tool:' + label[:40]) for label, t in tools])
            for s, name in moments:
                try:
                    print(f'   {path.film(s):7.2f}  {name}  (capture {s:.2f})')
                except SystemExit:
                    pass
            for s0, s1, tops, top0 in locks:
                values = [tops[i] - top0 for i in tops]
                print(f'   lock {path.film(s0):.2f}–{path.film(s1):.2f}: stage moves {min(values):+.0f} to {max(values):+.0f} px, '
                      f'and is held still')
            continue
        if args.only and clip['id'] != args.only:
            continue
        work = Path(tempfile.mkdtemp(prefix=f'screen-{clip["id"]}-'))
        background = None
        def picture(s):
            """The capture frame shown at capture time s, moved by its stage lock."""
            nonlocal background
            i, d = frame_at(s), shift(s)
            image = Image.open(files[i]).convert('RGB')
            if not d:
                return image
            background = background or image.getpixel((1580, 450))  # the page's own background colour
            moved = Image.new('RGB', image.size, background)
            moved.paste(image, (0, -d))
            return moved
        for k in (range(0) if args.camera_only else range(n)):
            t = film_times[k]
            i = path.shot_at(t)
            shot, link = path.shots[i], work / f'{k:05d}.jpg'
            first = shot['segments'][0][0]
            if shot['blend'] and i > 0 and t < first + shot['blend'] / 2:
                a = (t - (first - shot['blend'] / 2)) / shot['blend']
                Image.blend(picture(path.source(t, i - 1)), picture(path.source(t, i)), a * a * (3 - 2 * a)).save(link, quality=95)
            elif shift(path.source(t, i)):
                picture(path.source(t, i)).save(link, quality=95)
            else:
                link.symlink_to(files[frame_at(path.source(t, i))])
        target = out / f'{clip["id"]}.mp4'
        if not args.camera_only:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(args.fps), '-i', str(work / '%05d.jpg'),
                            '-vf', f'scale={OUT_SIZE[0]}:{OUT_SIZE[1]}:flags=lanczos,format=yuv420p', '-c:v', 'libx264',
                            '-preset', 'slow', '-crf', str(args.crf), '-g', str(args.fps), '-movflags', '+faststart',
                            '-an', str(target)], check=True)
            print(f'{clip["id"]}: {n} frames, {target.stat().st_size / 1e6:.1f} MB, film {path.start:.2f}–{path.end:.2f}')
        shutil.rmtree(work)

        def lapse(begin, end, clock):
            (start, shot), finish = path.locate(src(begin)), path.film(src(end))
            # Film/capture breakpoints of its shot: the pill's clock and speed read them.
            anchors = [[round(f, 3), round(s, 3)] for f0, f1, s0, s1 in shot['segments'] for f, s in ((f0, s0), (f1, s1))]
            return {'from': round(start, 3), 'to': round(finish, 3), 'clockFrom': round(src(clock), 3), 'anchors': anchors}
        manifest['clips'][clip['id']] = {
            'file': f'assets/screen/{clip["id"]}.mp4', 'start': round(path.start, 3), 'end': round(path.end, 3),
            'take': clip['take'], 'timelapses': [lapse(*r) for r in clip['timelapse']],
            'locks': [[round(path.film(s0), 3), round(path.film(s1), 3)] for s0, s1, *_ in locks],
            'keys': [[round(k[0], 3), *k[1:]] for k in keys], 'camera': camera,
        }
    if not args.report:
        manifest_path.write_text(json.dumps(manifest, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    main()
