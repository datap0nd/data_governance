"""Korean subtitles for the film: assets/subtitles-ko.json (read by scripts/build.mjs for index-ko.html) and
subtitles/wizard-ko.vtt and .srt for players.

Each narration line's "ko" is its Korean subtitle. A "|" splits it into parts shown one after another; "koSplit" names,
for each split, the English word whose spoken start begins the next part (its offset in the line's voice bank), so a
part changes as the narrator moves on. A line is shown from its start until its end plus a short hold, never into the
next line. "koTop" shows a line at the top of the frame instead (when the action it describes is at the bottom).

  python scripts/subtitles.py      # after audio/remix.py, before node scripts/build.mjs"""
import json
from pathlib import Path

FILM = Path(__file__).resolve().parents[1]
HOLD, MIN_GAP = 0.35, 0.08  # seconds a subtitle stays after its line ends; the least gap kept before the next one


def stamp(t, comma=False):
    h, rest = divmod(round(t * 1000), 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f'{h:02}:{m:02}:{s:02}{"," if comma else "."}{ms:03}'


def main():
    narration = json.loads((FILM / 'narration.json').read_text(encoding='utf-8'))
    timing = json.loads((FILM / 'audio/narration-timing.json').read_text(encoding='utf-8'))
    banks = {}
    events = []
    for i, (line, spoken) in enumerate(zip(narration, timing)):
        if 'ko' not in line:
            raise SystemExit(f'Line {i + 1} has no Korean subtitle ("ko").')
        bank = banks.setdefault(line['voice'], json.loads((FILM / f'audio/voice-{line["voice"]}.json').read_text(encoding='utf-8'))['lines'])
        words = next(b for b in bank if b['spoken'] == line.get('spoken', line['en']))['cues']
        parts, splits = line['ko'].split('|'), line.get('koSplit', [])
        if len(parts) != len(splits) + 1:
            raise SystemExit(f'Line {i + 1}: {len(parts)} parts need {len(parts) - 1} split words, not {len(splits)}.')
        starts = [spoken['start']] + [round(line['start'] + words[w] - 0.1, 3) for w in splits]
        nxt = timing[i + 1]['start'] - MIN_GAP if i + 1 < len(timing) else spoken['end'] + 1.5
        ends = starts[1:] + [round(min(spoken['end'] + HOLD, nxt), 3)]
        for k, (text, a, b) in enumerate(zip(parts, starts, ends)):
            if b - a < 0.8:
                raise SystemExit(f'Line {i + 1}, part {k + 1} would show for only {b - a:.2f} s.')
            events.append({'line': i + 1, 'start': round(a, 3), 'end': round(b, 3), 'text': text.strip(),
                           'top': bool(line.get('koTop'))})
    for a, b in zip(events, events[1:]):
        if b['start'] < a['end'] - 1e-6:
            raise SystemExit(f'Subtitles overlap at {b["start"]:.2f}: "{a["text"]}" and "{b["text"]}".')
        # Parts of one line follow each other directly: swap the text, don't fade between them.
        a['joinNext'] = b['joinPrev'] = b['start'] - a['end'] < 0.05
    (FILM / 'assets/subtitles-ko.json').write_text(json.dumps(events, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    out = FILM / 'subtitles'
    out.mkdir(exist_ok=True)
    (out / 'wizard-ko.vtt').write_text('WEBVTT\n\n' + ''.join(
        f'{k + 1}\n{stamp(e["start"])} --> {stamp(e["end"])}{" line:8%" if e["top"] else ""}\n{e["text"]}\n\n'
        for k, e in enumerate(events)), encoding='utf-8')
    (out / 'wizard-ko.srt').write_text(''.join(
        f'{k + 1}\n{stamp(e["start"], True)} --> {stamp(e["end"], True)}\n{e["text"]}\n\n'
        for k, e in enumerate(events)), encoding='utf-8')
    print(f'{len(events)} Korean subtitles for {len(narration)} lines, {events[0]["start"]:.2f}–{events[-1]["end"]:.2f} s')


if __name__ == '__main__':
    main()
