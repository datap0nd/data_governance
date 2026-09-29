# Metronome — Your data, connected

The current film is 144 seconds, 1920×1080 at 30 fps, with English narration and instrumental music. It has **no subtitles**, corner headings or secondary source/hub captions.

- [Watch the MP4](renders/review/animatic.mp4) or [open the chapter review](animatic-review.html).
- [Scene frames](review.html), [narration and pronunciation script](narration.json), [storyboard](STORYBOARD.md), [asset ledger](assets/LEDGER.md), [validation](QA.md).

## Story

1. Two distinct Korean report portals: ASAP and GSCM, with reporting periods, YoY and up/down variance triangles.
2. ASAP, GSCM, Bigdata portal, Datahub, NERP and Email reports flow through a centered Metronome icon/name into organized datasets.
3. Data ready for Excel, reporting and analysis using the user's own AI tools. No shared-key claim.
4. Code gives way to a visual Flow builder: website, data, destination, schedule, then a repeatable pipeline.
5. Six animated local AI agents inspect source, Flow and dataset stages at 02:00 AM Dubai time, leaving five issue flags and one healthy marker.
6. Five findings appear. A cursor selects one, opening its issue, evidence and proposed fix for review.
7. The METO AI Portal hosts the dashboard. Metronome supplies freshness and ETL through an API outside that portal UI.
8. An internal server connects to a Metronome website in Chrome, making access explicit.
9. ChatGPT, Claude and Gemini cycle through a Metronome MCP connection; each gains an enlarged halo and data/freshness/Flow symbols.

## Edit and render

Node.js 24 and the pinned HyperFrames 0.8.81 package are used. Edit `scripts/build.mjs`, `compositions/film.css`, `compositions/runtime.js` and `narration.json`.

```powershell
npm ci
node scripts/build.mjs
.\film.ps1 check --at '6,18,31,39,47,59,73,81,88,106,117,126,133,140' --strict --json
.\film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

Open `http://127.0.0.1:4392/animatic-review.html`. Set `HYPERFRAMES_BROWSER_PATH` to the installed Chrome executable if managed-browser discovery stalls. FFmpeg/FFprobe can be selected through `HYPERFRAMES_FFMPEG_PATH` and `HYPERFRAMES_FFPROBE_PATH`.

Install `audio/requirements.txt` and run `python audio/score.py` to regenerate narration/music. This uses network-based Edge TTS and cached phrases keyed by voice, rate and spoken text. `en` is readable copy; `spoken` guides continuous acronym pronunciation without full stops between letters. The English-only Andrew voice replaces the multilingual version. Word-boundary metadata is cached for review. A sentence exceeding its slot raises an error; it is never accelerated or truncated. The audio master is 48 kHz stereo 24-bit PCM and matches `timeline.json` duration. No SRT files are generated.

## Scope and fidelity

This film presents the owner's requested product story. The METO AI Portal/API architecture, Metronome MCP connections and simplified no-code journey are illustrative product direction, not evidence that those integrations are deployed. No application implementation is included. The overnight checks reflect `docs/data_auditor.md`; suggested fixes remain proposals for a person to review.

Portal data, issue examples and `.example` addresses are fictional. Chrome is a code recreation, not a verified pixel-identical capture of a particular release. Excel's ribbon comes from the owner's Scribble tour. Korean text is used only inside the report portals, with embedded Noto Sans KR. No native desktop automation is used.
