# Metronome + Wizard — From portal to decision

Current cut: **120 seconds / 2:00, 1920×1080, 30 fps**, English narration and background music, zero subtitles, light-mode UIs. Revision 11 turns the film into a joint showcase. Metronome collects portal reports and loads them into PostgreSQL on a schedule. Wizard, the executive analyst, answers cross-system questions over those tables, NERP and ASAP, with every number traced to its source.

[Watch](animatic-review.html) · [MP4](renders/review/animatic.mp4) · [Scene frames](review.html) · [Scene preview](scene-preview.html) · [Try the Flow builder](builder-demo.html) · [Narration](narration.json) · [Storyboard](STORYBOARD.md) · [QA](QA.md) · [Sources](assets/LEDGER.md)

## Story

Seven scenes. Metronome's half keeps the existing opening: portals, the extract/transform/load work, Metronome's pipeline controls and the working no-code Flow builder, now loading into PostgreSQL. A bridge shows leaders' questions wiring across NERP, GSCM, ASAP and the PostgreSQL tables Metronome keeps fresh. Wizard's half recreates its web app: the empty state, a typed question, the live step timeline (NERP spend, a read-only PostgreSQL query, ASAP share), the answer and chart, Check my data, and the evidence drawer showing the query Gemini wrote. The closing joins both products into one workflow and a paired lockup.

Revision 10's overnight agents, findings, browser/MCP access and ChatGPT comparison are removed; they remain in git history.

The builder uses actual input/dropdown/click handlers. It offers 12 reports, Shared drive / Email / PostgreSQL and four schedules. The film replays those events with deterministic typing, pointer dwell and press/release timing. The standalone demo creates no backend Flow.

## Build

Edit scripts/build.mjs, compositions/film.css, compositions/wizard.css, compositions/wizard.js, compositions/builder.js, compositions/runtime.js and narration.json. Use Node 24 and pinned HyperFrames 0.8.142. Build, synthesize speech, then build again so the animation reads the new word cues.

```powershell
node scripts/build.mjs
python audio/score.py
node scripts/build.mjs
$env:HYPERFRAMES_BROWSER_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.\film.ps1 check --at '4,15,26,36,44,50,55,60,66,72,77,81,84.4,88,92,95.6,98,100.5,104,110,115,119' --json
.\film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

Install audio/requirements.txt into an ignored `.venv` for the voice step; set FILM_FFMPEG_PATH if FFmpeg is not on PATH. The voice is en-US-AndrewNeural at −3%, with letter-by-letter guidance for ASAP, GSCM, NERP and UAE. Overlong lines fail rather than being accelerated or truncated. The 48 kHz stereo 24-bit master matches timeline duration.

`audio/score.py` also writes [assets/cues.json](assets/cues.json): film times for named words (extract, Build, NERP spend, Metronome, Egypt, Check, evidence…) from the synthesizer's word boundaries, shifted by the same leading trim as the audio. Animations key off those cues, so a re-voiced line keeps its visuals in sync.

## Scope and fidelity

Interfaces, data, prompts and outcomes are synthetic. Wizard's screens are recreated from its web app source (`apps/web/src`) and its replay demo, run locally in fixture mode on 2026-10-09; figures come from its synthetic CEO transcript. The film shows Wizard's live labels ("gemini-3.8-flash", "Live") as they appear with PostgreSQL connected; the sell-out step queries a Metronome-loaded view, which is the intended joint workflow rather than a recorded run. The SQL shown is illustrative. Chrome is recreated in code. asap-portal.com is the requested display address, not a contacted site.

The film calls Wizard's measure an investment-efficiency proxy, never ROI, matching Wizard's own wording. It makes no claim about measured accuracy or time savings. No app implementation or deployment verification is included.
