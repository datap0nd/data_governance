# Wizard — Every source, one answer

Current cut: **81 seconds / 1:21, 1920×1080, 30 fps**, English narration and background music, no subtitles, light mode. Revision 12 is a concept film for review. It tells one story for a non-technical audience: our data is spread across many portals and files, and Wizard brings it together so anyone can ask a question in plain words and get a checked, visual answer.

[Watch](animatic-review.html) · [MP4](renders/review/animatic.mp4) · [Scene frames](review.html) · [Scene preview](scene-preview.html) · [Narration](narration.json) · [Storyboard](STORYBOARD.md) · [QA](QA.md) · [Sources](assets/LEDGER.md)

## Story

1. **Data everywhere:** ASAP, GSCM, BDP and NERP, each packed with reports, plus Excel files, emails, presentations and PDFs. No counts are given; the lists rush past instead.
2. **Hunting for one answer:** someone opens report after report while the clock runs.
3. **Everything in Wizard:** every source flows into Wizard, whose local AI knows where each answer lives.
4. **Just ask:** a clean Wizard screen; the question is typed in plain words.
5. **Wizard finds the reports:** sell-out from GSCM, market share from ASAP, spend from NERP, targets from Excel.
6. **A complete, visual answer:** summary, key numbers, a bar chart and a market-share donut.
7. **Checked and safe:** every number traced to its source report; authorized AI that shows only what you're allowed to see.
8. **Every source. One answer.**

Metronome and the Flow builder are not in this cut; revisions 10 and 11 remain in git history.

## Build

Edit scripts/build.mjs, compositions/wizard.js, compositions/film.css, compositions/runtime.js and narration.json. Use Node 24 and pinned HyperFrames 0.8.142. Build, synthesize speech, then build again so the animation reads the new word cues.

```powershell
node scripts/build.mjs
python audio/score.py
node scripts/build.mjs
.\film.ps1 check --at '3,7.5,10,14,17.5,19.5,21.5,23.5,26,30,34,36.5,39,41.5,44,47,50,53,55,58,60.5,64,68,71,73.5,76,79.5' --json
.\film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

HyperFrames renders with its bundled headless browser (`hyperframes browser ensure`); on Windows, pointing HYPERFRAMES_BROWSER_PATH at desktop Chrome can open a real browser window when no Chrome is running. Install audio/requirements.txt into an ignored `.venv` for the voice step. The voice is en-US-AndrewNeural at −3%, with the owner's pronunciation: ASAP said as a word, NERP as "N-ERP", and GSCM, BDP and PDF as smooth initialisms (written unspaced; spaced letters sound choppy). Overlong lines fail rather than being sped up or cut. `audio/score.py` writes [assets/cues.json](assets/cues.json), the film time of each named word, and every speech-linked reveal reads it; `"P@3"` would name a word's third occurrence.

## Scope and fidelity

A concept film. The Wizard screens are a simplified presentation of the Wizard app (its mark, cobalt accent and Inter type), with technical controls left out. Report names and all figures are illustrative; no report counts appear. The narration says "local AI" and "authorized AI" at the owner's direction, and "has learned our portals" for Wizard's knowledge of which report holds what. No accuracy or time-saving claim is measured.
