"""The music bed shared by audio/score.py and audio/remix.py: the track, trimmed or extended to the film, faded."""
import re
import subprocess

# Happy Beats & Business Moves Vol. 11 runs 87.6 s at about 114 bpm (a bar is 2.11 s). A longer film repeats eight bars:
# at 45 s, under narration where the bed is ducked, the music crossfades back 16.865 s to the matching beat.
SPLICE_AT, SPLICE_BACK, CROSSFADE = 45.0, 16.865, 1.0


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
    if duration > music_seconds + SPLICE_BACK - CROSSFADE:
        raise ValueError(f'The film ({duration} s) is longer than the extended music bed; add another splice.')
    head, tail = SPLICE_AT + CROSSFADE / 2, SPLICE_AT - SPLICE_BACK - CROSSFADE / 2
    return (f'[1:a]asplit=2[ma][mb];[ma]atrim=0:{head},asetpts=PTS-STARTPTS[m1];[mb]atrim={tail},asetpts=PTS-STARTPTS[m2];'
            f'[m1][m2]acrossfade=d={CROSSFADE}:c1=qsin:c2=qsin,atrim=0:{duration},asetpts=PTS-STARTPTS,{fades}')
