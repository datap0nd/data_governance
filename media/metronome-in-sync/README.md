# Metronome — Ready by morning

The current 84-second presentation film has English narration, burned-in Korean subtitles, continuous instrumental background music and light interfaces. Metronome is a static brand icon. The story follows sources → Metronome → organized datasets → scheduled Flows → overnight agents → evidence → a morning dashboard.

- [Watch the MP4](renders/review/animatic.mp4), 1920×1080, 30 fps, H.264/AAC.
- [Review with scene buttons](animatic-review.html) or [view eight frames](review.html).
- [English script and Korean translation](narration.json), [Korean SRT](subtitles.ko.srt), [English SRT](subtitles.en.srt).
- [Storyboard](STORYBOARD.md), [asset sources](assets/LEDGER.md), [validation](QA.md).

## Edit and render

Use Node.js 24. All editable visuals live in `scripts/build.mjs`, `compositions/film.css` and `compositions/runtime.js`; narrative cue timings live in `narration.json`. The build generates `index.html` and `timeline.json`.

```powershell
npm ci
node scripts/build.mjs
.\film.ps1 check --at '3,17,25,35,51,59,70,81' --strict --json
.\film.ps1 render --fps 30 --quality looks --workers 2 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

Review at `http://127.0.0.1:4392/animatic-review.html`. The server supports MP4 seeking. Set `FILM_REVIEW_PORT` for another port. FFmpeg and FFprobe must be on PATH; HyperFrames also accepts `HYPERFRAMES_FFMPEG_PATH` and `HYPERFRAMES_FFPROBE_PATH`.

To regenerate narration and music, install `audio/requirements.txt` in the film's own Python environment, then run `python audio/score.py`. It uses Microsoft Edge TTS's synthetic `en-US-AndrewMultilingualNeural` voice and needs network access. Cached phrases go under ignored `.audio-cache/`. Set `FILM_FFMPEG_PATH` if needed. The score master is 48 kHz stereo 24-bit PCM. Keep source MP3 attribution when sharing the film.

The Excel ribbon reuses pixels from the owner's linked Scribble product tour. Chrome is recreated in HTML/CSS using its Windows tab strip, omnibox and controls; it is not a native screen recording or a verified pixel-perfect capture. UI content and data are illustrative. Local Segoe UI is used when available on Windows; other operating systems may render native UI labels differently. Korean subtitles embed a subset of Noto Sans KR under OFL.

No live portal, real business data or desktop automation is involved. The overnight auditor reflects `docs/data_auditor.md`: read-only checks and evidence-linked findings, with human review. The final AI dashboard is an illustrative reporting workflow.
