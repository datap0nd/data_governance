# Metronome — In Sync

This folder contains the editable HyperFrames project and the current **84-second quiet presentation cut**. The film is a fictional product illustration; its source systems, reports, numbers and AI journey are illustrative. The AI sequence is visibly labeled “Concept preview.”

- [Watch the MP4](renders/review/animatic.mp4) (1920×1080, 60 fps, H.264/AAC).
- [Open the review page](animatic-review.html) or [browse eight style frames](review.html).
- Edit [timeline.json](timeline.json), [scene builder](scripts/build.mjs), [visual runtime](compositions/runtime.js), [styles](compositions/film.css), and [score source](audio/score.py).
- Read the [storyboard](STORYBOARD.md), [visual direction](frame.md), [supers](SUPERS.md), [asset ledger](assets/LEDGER.md), and [validation record](QA.md).

The committed MP4 and 24-bit score are the reviewed output. Build dependencies, discarded photo candidates, prior renders, temporary frames, caches and local environments are intentionally excluded. The audio WAV is kept because HyperFrames uses it when rendering the editable composition.

## Work on the film

From this directory, use Node.js 24 and npm:

```powershell
npm ci
node scripts/build.mjs
.\film.ps1 check --at '18,30.5,42,53.5,70' --strict --json
.\film.ps1 preview
```

The project pins HyperFrames 0.8.81, GSAP 3.14.2, Three.js 0.181.2 and esbuild 0.25.12. The supplied MP4 was rendered with HyperFrames 0.8.78 before this source package was brought into GitHub; the 0.8.81 project passes the same focused composition checks, but a future render may not be pixel-identical.

For the review website, run `node scripts/serve-review.mjs` and open `http://127.0.0.1:4392/animatic-review.html`. Set `FILM_REVIEW_PORT` to use another local port. The server supports MP4 byte ranges for seeking.

To regenerate the score, install Python 3.13, FFmpeg and the film's own NumPy environment:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r audio\requirements.txt
.\.venv\Scripts\python.exe audio\score.py
```

`audio/score.py` finds `ffmpeg` on PATH; `FILM_FFMPEG_PATH` may name a specific executable. The generated stem WAVs are ignored. Rebuild and render after changing the timeline, visuals or score; the committed MP4 is a review artifact, not an automatically updated build output.
