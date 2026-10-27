"""Cut the real Wizard screen recordings (capture/) into the film's screen clips.

A clip is a path through one recorded take. It starts at a film time and a capture moment; each step then moves to a
later capture moment at real speed (1), sped up (a speed above 1: the timelapse, used only while Wizard is working),
or held over a given film duration. A cut jumps to another capture moment and may cross-dissolve. Capture moments are
markers the recorder logged, or the start of a tool call as Wizard logged it, so the timelapse follows the real run.
Frames are the capture's own screencast frames, never redrawn. Writes assets/screen/<clip>.mp4 and assets/screen.json
(clip windows, timelapse ranges and camera keyframes for compositions/runtime.js).

  python scripts/screen.py [--takes capture/takes] [--report] [--camera-only]"""
from __future__ import annotations
import argparse, bisect, json, re, shutil, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from PIL import Image

FILM = Path(__file__).resolve().parents[1]
PAGE = (1600, 900)  # the capture viewport in CSS pixels; camera keyframes use these coordinates
OUT_SIZE = (3200, 1800)  # the capture's device pixels (2x), kept so camera push-ins stay sharp
LAPSE = 2.25  # timelapse speed while Wizard reads the systems: their calls, 4.3 s apart, land on the narrated names
CHECK_LAPSE = 2  # the check, slow enough to follow its stage and its six MATCH rows arriving

# Capture references: "<marker>" or "tool:<label start>", with an optional "+-seconds". Film references: a number,
# "cue:<name>[+-s]", or "src:<capture reference>[@+-s]" (the film time this clip reaches that capture moment).
# Steps: [capture, speed], [capture, "hold", film seconds], [capture, "until", film reference], or a cut
# {"cut": capture, "blend": seconds}. A clip may start "after:<clip>-s", overlapping the previous one for the hand-off.
# Timelapses: [from, to, clock] capture references for each stretch where Wizard works; the pill's clock counts from clock.
CLIPS = [
    {"id": "ask", "take": "quarter", "start": [33.2, "start+2.3"],
     "path": [["sent", 1],                                  # attach the file, type, send: real time
              ["sent+3.2", 1],                              # Wizard appears and starts work: real time
              ["tool:Queried attached file", LAPSE],        # timelapse: GSCM, ASAP, NERP, then the file
              ["done", 4.5],                                # calculating, charting, writing
              ["done+2.1", 1],                              # the answer lands: the wizard hops
              {"cut": "settled-0.2", "blend": 0.4}, ["top", 1], ["top+0.3", "until", "cue:charts+2.0"]],
     "timelapse": [["sent+3.2", "done", "sent"]],
     # The camera moves at its own easy pace; while the timelapse runs it holds the stage and only drifts closer. At
     # zoom z the frame's top edge stays just below the conversation title (page y 45): fy = 45 + 540 / z.
     "camera": [["src:start+2.3", 800, 450, 1], ["src:attach_click@-0.9", 800, 450, 1],
                ["src:attach_click@+0.3", 936, 780, 1.55], ["src:sent@+0.2", 936, 780, 1.55],
                ["src:sent@+1.4", 936, 417, 1.45], ["src:tool:Queried attached file", 936, 393, 1.55],
                ["src:done@-0.8", 936, 393, 1.55], ["src:done@+0.2", 936, 430, 1.12],
                ["src:done+2.1", 936, 450, 1.05], ["src:top", 936, 450, 1.1], ["cue:summary-0.2", 936, 300, 1.5],
                ["cue:numbers-0.4", 936, 300, 1.5], ["cue:numbers+0.5", 936, 725, 1.45],
                ["cue:charts-0.2", 936, 725, 1.45], ["cue:charts+0.7", 936, 470, 1.42], ["cue:charts+1.0", 936, 470, 1.4],
                ["cue:charts+1.7", 800, 450, 1]]},
    {"id": "stock", "take": "stock", "start": ["after:ask-0.8", "start+2.3"],
     "path": [["sent", 1], ["sent+3.1", 1], ["done", 6], ["done+1.9", 1],
              {"cut": "settled-0.1", "blend": 0.4}, ["top", 1],
              ["check_click+0.1", 1],                       # read the answer, scroll down, click Check my data: real time
              ["check_click+3.1", 1],                       # the check appears and starts work: real time
              ["check_done", CHECK_LAPSE],                  # timelapse
              ["check_done+1.6", 1],                        # six MATCH rows: real time
              {"cut": "citation_click-0.3", "blend": 0.35}, ["evidence+1.2", 1],
              ["evidence+1.8", "until", "cue:stop-0.31"]],
     "timelapse": [["sent+3.1", "done", "sent"], ["check_click+3.1", "check_done", "check_click"]],
     "camera": [["src:start+2.3", 800, 450, 1], ["src:type_start@-0.2", 936, 800, 1.55], ["src:sent@+0.2", 936, 800, 1.55],
                ["src:sent@+1.4", 936, 417, 1.45], ["src:done@-0.6", 936, 393, 1.55], ["src:done@+0.3", 936, 430, 1.12],
                ["src:done+1.9", 936, 450, 1.08], ["src:top", 936, 380, 1.4], ["src:top@+1.2", 936, 400, 1.46],
                ["src:top+2.3", 936, 400, 1.46], ["src:bottom", 700, 640, 1.35], ["src:check_click@-0.3", 700, 690, 1.42],
                ["src:check_click@+1.2", 936, 417, 1.45], ["src:tool:Checked@+0.8", 936, 400, 1.52],
                ["src:check_done@-1.0", 936, 560, 1.45], ["src:check_done+1.6@-0.1", 936, 580, 1.5],
                ["src:citation_click-0.3@+0.2", 1180, 420, 1.15], ["src:evidence", 1240, 560, 1.3],
                ["src:evidence+1.2", 1240, 600, 1.38], ["cue:stop-0.31", 1240, 600, 1.45]]},
]


def load_take(root: Path, take: str):
    frames = json.loads((root / take / 'frames.json').read_text()) if (root / take / 'frames.json').exists() else None
    meta = json.loads((root / take / 'markers.json').read_text())
    t0 = meta['markers']['start']['t']
    events = {k: v['t'] - t0 for k, v in meta['markers'].items()}
    tools = [(e['label'] or '', datetime.fromisoformat(e['ts']).timestamp() - t0)
             for r in meta['runs'] for e in r['events'] if e['type'] == 'tool_started']
    times = [f['t'] - t0 for f in frames['frames']] if frames else []
    files = [root / take / 'frames' / f['file'] for f in frames['frames']] if frames else []
    return events, tools, times, files


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
            if step[1] == 'hold':
                length = step[2]
            elif step[1] == 'until':
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
    cues = json.loads(cues_file.read_text())['cues'] if cues_file.exists() and not args.report else {}
    takes, out = Path(args.takes), FILM / 'assets' / 'screen'
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = FILM / 'assets/screen.json'
    manifest = {'page': PAGE, 'clips': {}}
    if args.only and manifest_path.exists():  # re-cut one clip; keep the others as they are
        manifest['clips'] = json.loads(manifest_path.read_text())['clips']
    ends = {}
    for clip in CLIPS:
        events, tools, times, files = load_take(takes, clip['take'])
        begin = clip['start'][0]
        if isinstance(begin, str):
            name, delta = offset(begin.removeprefix('after:'))
            begin = ends[name] + delta
        path = Path_(clip, begin, events, tools, cues, args.fps)
        ends[clip['id']] = path.end
        if args.report:
            print(f'{clip["id"]}: film {path.start:.2f}–{path.end:.2f}')
            moments = sorted([(v, k) for k, v in events.items()] + [(t, 'tool:' + label[:40]) for label, t in tools])
            for s, name in moments:
                try:
                    print(f'   {path.film(s):7.2f}  {name}  (capture {s:.2f})')
                except SystemExit:
                    pass
            continue
        if args.only and clip['id'] != args.only:
            continue
        n = round((path.end - path.start) * args.fps)
        frame_at = lambda s: files[max(0, bisect.bisect_right(times, s) - 1)]
        work = Path(tempfile.mkdtemp(prefix=f'screen-{clip["id"]}-'))
        for k in (range(0) if args.camera_only else range(n)):
            t = begin + (k + .5) / args.fps
            i = path.shot_at(t)
            shot, link = path.shots[i], work / f'{k:05d}.jpg'
            first = shot['segments'][0][0]
            if shot['blend'] and i > 0 and t < first + shot['blend'] / 2:
                a = (t - (first - shot['blend'] / 2)) / shot['blend']
                Image.blend(Image.open(frame_at(path.source(t, i - 1))).convert('RGB'),
                            Image.open(frame_at(path.source(t, i))).convert('RGB'), a * a * (3 - 2 * a)).save(link, quality=95)
            else:
                link.symlink_to(frame_at(path.source(t, i)))
        target = out / f'{clip["id"]}.mp4'
        if not args.camera_only:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(args.fps), '-i', str(work / '%05d.jpg'),
                            '-vf', f'scale={OUT_SIZE[0]}:{OUT_SIZE[1]}:flags=lanczos,format=yuv420p', '-c:v', 'libx264',
                            '-preset', 'slow', '-crf', str(args.crf), '-g', str(args.fps), '-movflags', '+faststart',
                            '-an', str(target)], check=True)
            print(f'{clip["id"]}: {n} frames, {target.stat().st_size / 1e6:.1f} MB, film {path.start:.2f}–{path.end:.2f}')
        shutil.rmtree(work)
        src = lambda ref: source_time(ref, events, tools)

        def lapse(begin, end, clock):
            (start, shot), finish = path.locate(src(begin)), path.film(src(end))
            # Film/capture breakpoints of its shot: the pill's clock and speed read them.
            anchors = [[round(f, 3), round(s, 3)] for f0, f1, s0, s1 in shot['segments'] for f, s in ((f0, s0), (f1, s1))]
            return {'from': round(start, 3), 'to': round(finish, 3), 'clockFrom': round(src(clock), 3), 'anchors': anchors}
        manifest['clips'][clip['id']] = {
            'file': f'assets/screen/{clip["id"]}.mp4', 'start': round(path.start, 3), 'end': round(path.end, 3),
            'take': clip['take'], 'timelapses': [lapse(*r) for r in clip['timelapse']],
            'camera': [[round(film_ref(c[0], cues, path, events, tools), 3), *c[1:]] for c in clip['camera']],
        }
    if not args.report:
        manifest_path.write_text(json.dumps(manifest, indent=1) + '\n')


if __name__ == '__main__':
    main()
