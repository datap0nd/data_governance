"""Burn English captions over the preserved clean master without changing audio."""
import os
from pathlib import Path
import shutil
import subprocess

film = Path(__file__).resolve().parents[1]
ffmpeg = os.environ.get("FILM_FFMPEG_PATH") or shutil.which("ffmpeg")
if not ffmpeg:
    raise SystemExit("Set FILM_FFMPEG_PATH or install FFmpeg with libass.")
subprocess.run(
    [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", "renders/review/animatic-clean.mp4",
        "-vf",
        "scale=1760:990:flags=lanczos,pad=1920:1080:80:0:color=0xf8faf9,"
        "ass=captions/en.ass:fontsdir=assets/fonts",
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", "renders/review/animatic-captioned-tmp.mp4",
    ],
    cwd=film,
    check=True,
)
(film / "renders/review/animatic-captioned-tmp.mp4").replace(
    film / "renders/review/animatic.mp4"
)
