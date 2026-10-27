# Wizard — Every source, one answer

Current cut: **109 seconds / 1:49, 1920×1080, 30 fps**, English narration and background music, no subtitles, light mode. Revision 13 is a concept film for review. It tells one story for a non-technical audience: our data is spread across many portals and files, and Wizard brings it together so anyone can ask a question in plain words and get a checked, visual answer. From the question on, the screens are **the real Wizard app**, recorded and shown in real time; only the stretch where Wizard is working is sped up, as a marked timelapse. Fast moves carry motion blur and a whoosh, and the key statements type in letter by letter.

[Watch](animatic-review.html) · [MP4](renders/review/animatic.mp4) · [Scene frames](review.html) · [Scene preview](scene-preview.html) · [Narration](narration.json) · [Storyboard](STORYBOARD.md) · [QA](QA.md) · [Sources](assets/LEDGER.md) · [Capture](capture/README.md)

## Story

1. **Data everywhere:** ASAP, GSCM, BDP and NERP, each packed with reports, plus Excel files, emails, presentations and PDFs. No counts are given; the lists rush past instead.
2. **Hunting for one answer:** the question types in large over the blurred clutter, docks into its search bar, and someone opens report after report while the clock runs.
3. **Everything in Wizard:** every source flows into Wizard's logo; its local AI knows where each answer lives.
4. **Just ask (real app):** Wizard's start screen grows out of the logo. "Q3 targets.xlsx" is attached and "How did we do this quarter against our targets?" is typed and sent, in real time.
5. **Wizard at work:** the pixel wizard appears and writes its plan in real time; about three seconds later the timelapse starts (a "Timelapse 2×" pill with the run's real clock, and a blue frame edge), and GSCM, ASAP, NERP and the Excel file light up as the narrator names them. Calculating, charting and writing go faster (5×).
6. **The answer:** back to real time as it lands with the wizard's hop; the camera moves over the summary, the table and the chart.
7. **A second question**, music only: "Where is stock building up in the channel?" typed in real time, its run as a 6× timelapse, ending on Egypt's Galaxy A37 and its chart.
8. **Checked**, in the same window: the view scrolls down and **Check my data** is clicked in real time; the check runs as a 2× timelapse; its six MATCH rows and then the Sources drawer, with the GSCM rows behind them, play in real time under "Every number is checked against its source report".
9. **Safe:** the app steps back behind "Secure by design".
10. **Stop searching. Start asking.** The statement pops in word by word, whips off as Wizard's pixel logo punches in, and "Every source. One answer." types in above every source.

## Build

The film is HTML rendered by pinned HyperFrames 0.8.142 (Node 24). Edit `compositions/wizard.js`, `compositions/film.css`, `compositions/runtime.js`, `scripts/screen.py` and `narration.json`.

```powershell
.venv\Scripts\python scripts\screen.py --report   # when each recorded moment appears; place narration.json lines from it
.venv\Scripts\python audio\remix.py               # narration from the revision 12 voice lines and the music bed; writes assets/cues.json
.venv\Scripts\python scripts\screen.py            # cut capture/takes into assets/screen/*.mp4 and assets/screen.json
node scripts/build.mjs                             # timeline.json and index.html with the cues, camera moves, motion times and sounds
.venv\Scripts\python audio\sfx.py                 # synthesize the whooshes and mix them into audio/score.wav
.\film.ps1 check --at '3,7.6,8,14,15,15.6,16.5,21.5,22.2,23.3,26,30,33.5,34,37,40,43,45.5,48,50,53,55,58.4,59.4,60.4,61.7,63,67,70.5,74,77,80,83,86.5,88.5,90.8,93,95.8,97.5,99.5,100.7,101.6,102.9,103.7,104.5,105.6,107.5' --json
.\film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

- **Voice.** Every line is the revision 12 recording (en-US-AndrewNeural at −3%, the owner's pronunciation: ASAP as a word, NERP as "N-ERP", smooth GSCM, BDP and PDF). `audio/remix.py` cuts each line from `audio/voice-rev12.flac` by `audio/voice-rev12.json` and places it at its new start, so no speech service is needed and the voice is unchanged; it leaves voice and music in `.audio-cache/premix.wav` for `audio/sfx.py`. A new or reworded line is voiced with `audio/voice.py <bank>` (edge-tts, same voice and settings) into its own line bank, which `audio/remix.py` places like the others; `audio/score.py` re-voices every line instead. Revision 13 drops the line with the Gulf figures, which the real answer does not match.
- **Screens.** `scripts/screen.py` describes each clip as a path through a recorded take: steps at real speed, a timelapse step only while Wizard works (from about three seconds after it appears until the answer is done), holds, and dissolving cuts. Capture moments are recorder markers or tool calls as Wizard logged them; `--report` prints when each appears in the film, which is how the narration lines are placed. During the timelapse Wizard's systems are 4.3 s apart, so at 2.25× they light up about 1.9 s apart, as the narrator names them. There are two takes and two windows: question 1, then question 2 with its check, so the check runs in the window that shows the answer it checks. Frames are the capture's own screencast frames at 3200 × 1800. The clips need `capture/takes/` (not committed: record them with [capture](capture/README.md)); `--camera-only` rewrites the camera moves without them.
- **Motion and sound.** `scripts/build.mjs` works out when every move starts (`motion` in `timeline.json`) and the sound that goes with it (`sfx`). `compositions/runtime.js` animates from those times: kinetic type pops each letter in larger, nudged and soft, then settles; motion blur is a directional Gaussian per moving element, sized every frame from how far it moved, so slow moves stay sharp and whips, sweeps and the lists' rush smear along their path. `audio/sfx.py` synthesizes the whooshes (seeded, no samples) and adds them to the voice-and-music premix through the same limiter, without make-up gain.
- **Music.** `audio/bed.py` trims or extends the track for both mixers. The film is longer than the 87.6 s track, so at 0:45 and again at 1:36, under narration, it crossfades back eight bars (16.865 s at about 114 bpm) to the matching beat.
- **Rendering.** HyperFrames decodes the clips with FFmpeg, frame-accurately. On Linux, point `HYPERFRAMES_BROWSER_PATH` at a chrome-headless-shell; on Windows, its bundled headless browser (`hyperframes browser ensure`) avoids opening desktop Chrome. Previewing in a browser needs one that plays H.264 (Chrome, Edge).

## Scope and fidelity

A concept film. Sections 1–3 and the end card are drawn for the film; sections 4–9 are the real Wizard web app (`main` at `ff4a9e2`), running locally in replay mode over Wizard's synthetic fixtures with Wizard's own tools, recorded and sped up. For the recording, Wizard's development labels for replay and synthetic data and its demo suggestions are hidden, and the film carries "Real Wizard app · illustrative data" instead. Figures come from synthetic data; the targets file is synthetic. The narration says "local AI" and "authorized AI" at the owner's direction. No accuracy or time-saving claim is measured. Typing, clicks, scrolling and the answers landing play in real time; the timelapse clock shows how long the recorded run took, paced by the capture server, not by a live Gemini.
