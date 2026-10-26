# Wizard — Every source, one answer

Current cut: **84 seconds / 1:24, 1920×1080, 30 fps**, English narration and background music, no subtitles, light mode. Revision 13 is a concept film for review. It tells one story for a non-technical audience: our data is spread across many portals and files, and Wizard brings it together so anyone can ask a question in plain words and get a checked, visual answer. From the question on, the screens are **the real Wizard app**, recorded and shown as timelapses.

[Watch](animatic-review.html) · [MP4](renders/review/animatic.mp4) · [Scene frames](review.html) · [Scene preview](scene-preview.html) · [Narration](narration.json) · [Storyboard](STORYBOARD.md) · [QA](QA.md) · [Sources](assets/LEDGER.md) · [Capture](capture/README.md)

## Story

1. **Data everywhere:** ASAP, GSCM, BDP and NERP, each packed with reports, plus Excel files, emails, presentations and PDFs. No counts are given; the lists rush past instead.
2. **Hunting for one answer:** someone opens report after report while the clock runs.
3. **Everything in Wizard:** every source flows into Wizard's logo; its local AI knows where each answer lives.
4. **Just ask (real app):** Wizard's start screen grows out of the logo. "Q3 targets.xlsx" is attached and "How did we do this quarter against our targets?" is typed.
5. **Timelapse, Wizard at work:** the pixel wizard works through the systems; GSCM, ASAP, NERP and the Excel file light up as the narrator names them. A pill counts the real run time (31 s) as it plays sped up.
6. **The answer:** it lands with the wizard's hop; the camera moves over the summary, the table and the chart.
7. **Timelapse, a second question:** "Where is stock building up in the channel?", music only, ending on Egypt's Galaxy A37 and its chart.
8. **Checked:** back to the first answer: **Check my data** re-checks six figures (all MATCH), then the Sources drawer shows the GSCM rows behind them.
9. **Safe:** the app steps back behind "Secure by design".
10. **Every source. One answer.** Wizard's pixel logo and every source.

## Build

The film is HTML rendered by pinned HyperFrames 0.8.142 (Node 24). Edit `compositions/wizard.js`, `compositions/film.css`, `compositions/runtime.js`, `scripts/screen.py` and `narration.json`.

```powershell
node scripts/build.mjs                      # timeline.json (the voice step reads its duration)
.venv\Scripts\python audio\remix.py         # narration from the revision 12 voice lines; writes assets/cues.json
.venv\Scripts\python scripts\screen.py      # cut capture/takes into assets/screen/*.mp4 and assets/screen.json
node scripts/build.mjs                      # index.html with the cues and camera moves
.\film.ps1 check --at '3,7.5,14,21.5,26,30,34,36.5,40,43,45,47,50,52.6,53.8,54.8,57,60,64.5,66.8,68.2,70,73,77,81' --json
.\film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

- **Voice.** Every line is the revision 12 recording (en-US-AndrewNeural at −3%, the owner's pronunciation: ASAP as a word, NERP as "N-ERP", smooth GSCM, BDP and PDF). `audio/remix.py` cuts each line from `audio/voice-rev12.flac` by `audio/voice-rev12.json` and places it at its new start, so no speech service is needed and the voice is unchanged. A new or reworded line needs `audio/score.py` (edge-tts), which voices every line in `narration.json` and writes the same cue file. Revision 13 drops the line with the Gulf figures, which the real answer does not match.
- **Screens.** `scripts/screen.py` maps film time to capture time with anchors: a narration cue, a capture marker, or a tool call as Wizard logged it. Between anchors time runs linearly, which makes the timelapse, and a system lights up exactly when the narrator names it. Frames are the capture's own screencast frames at 3200 × 1800; a cut inside a clip may dissolve. The clips need `capture/takes/` (not committed: record them with [capture](capture/README.md)); `--camera-only` rewrites the camera moves without them.
- **Rendering.** HyperFrames decodes the clips with FFmpeg, frame-accurately. On Linux, point `HYPERFRAMES_BROWSER_PATH` at a chrome-headless-shell; on Windows, its bundled headless browser (`hyperframes browser ensure`) avoids opening desktop Chrome. Previewing in a browser needs one that plays H.264 (Chrome, Edge).

## Scope and fidelity

A concept film. Sections 1–3 and the end card are drawn for the film; sections 4–9 are the real Wizard web app (`main` at `ff4a9e2`), running locally in replay mode over Wizard's synthetic fixtures with Wizard's own tools, recorded and sped up. For the recording, Wizard's development labels for replay and synthetic data and its demo suggestions are hidden, and the film carries "Real Wizard app · illustrative data" instead. Figures come from synthetic data; the targets file is synthetic. The narration says "local AI" and "authorized AI" at the owner's direction. No accuracy or time-saving claim is measured; the timelapse clock shows how long the recorded run took.
