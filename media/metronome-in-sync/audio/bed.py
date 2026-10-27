"""The music bed shared by audio/score.py and audio/remix.py: the track, trimmed or extended to the film, faded."""
import re
import subprocess

# Happy Beats & Business Moves Vol. 11 runs 87.6 s at about 114 bpm (a bar is 2.11 s). A longer film repeats eight bars:
# at each splice (a film time, under narration where the bed is ducked) the music crossfades back 16.865 s to the
# matching beat. Every splice lands where the track is most like itself eight bars earlier, track 45 s, each time.
SPLICES, SPLICE_BACK, CROSSFADE = (45.0, 61.865, 78.73), 16.865, 1.0


def seconds(ff, path):
    """Length of a media file, read from FFmpeg's own report (no ffprobe needed)."""
    report = subprocess.run([ff, '-hide_banner', '-i', str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r'Duration: (\d+):(\d+):([\d.]+)', report).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def music_filter(duration, music_seconds):
    """Filter graph from input 1 (the track) to [m]: the bed at its mix level, faded in and out."""
    fades = f'volume=0.12,afade=t=in:d=1,afade=t=out:st={duration - 4}:d=4[m]'
    if duration <= music_seconds:
        return f'[1:a]atrim=0:{duration},asetpts=PTS-STARTPTS,{fades}'
    count = next((k for k in range(1, len(SPLICES) + 1) if duration <= music_seconds + k * (SPLICE_BACK - CROSSFADE)), None)
    if count is None or SPLICES[count - 1] >= duration:
        raise ValueError(f'The film ({duration} s) is longer than the extended music bed; add another splice.')
    # Track time at each splice (the bed has already jumped back once per earlier splice), and the pieces between them.
    track = [at - k * SPLICE_BACK for k, at in enumerate(SPLICES[:count])]
    cuts = [0.0] + [t - SPLICE_BACK - CROSSFADE / 2 for t in track]
    ends = [t + CROSSFADE / 2 for t in track] + [music_seconds]
    pieces = ''.join(f'[s{i}]atrim={round(a, 3)}:{round(b, 3)},asetpts=PTS-STARTPTS[p{i}];' for i, (a, b) in enumerate(zip(cuts, ends)))
    chain, last = '', 'p0'
    for i in range(1, count + 1):
        chain += f'[{last}][p{i}]acrossfade=d={CROSSFADE}:c1=qsin:c2=qsin[x{i}];'
        last = f'x{i}'
    return (f'[1:a]asplit={count + 1}' + ''.join(f'[s{i}]' for i in range(count + 1)) + ';' + pieces + chain +
            f'[{last}]atrim=0:{duration},asetpts=PTS-STARTPTS,{fades}')
