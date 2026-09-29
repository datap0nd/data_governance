# Metronome — Your data, connected

Current cut: **104 seconds, 1920×1080, 30 fps**, English narration and background music, zero subtitles, light-mode UIs. Revision 6 is 40 seconds shorter than revision 5. Measured gaps between generated sentences fall from 63.067 to 31.154 seconds (50.6% reduction); speech is neither sped up nor cut.

[Watch](animatic-review.html) · [MP4](renders/review/animatic.mp4) · [Scene frames](review.html) · [Try the Flow builder](builder-demo.html) · [Narration](narration.json) · [Storyboard](STORYBOARD.md) · [QA](QA.md) · [Sources](assets/LEDGER.md)

## Story

Eight scenes: reports across portals; organized data; slow code beside completed no-code creation; independent overnight agents; five actionable findings; powered HTML dashboards; browser or AI-prompt access; concrete ChatGPT before/after and a comparison table. The repeated Excel/AI-question scene, METO portal section, internal-server diagram and rotating AI-logo equation are removed.

The builder uses actual input/dropdown/click handlers. It offers 12 reports, Shared drive / Documents / Local database and four schedules. The film replays those events with deterministic typing, pointer dwell and press/release timing. The standalone demo creates no backend Flow.

## Build

Edit scripts/build.mjs, compositions/film.css, compositions/builder.js, compositions/runtime.js and narration.json. Use Node 24 and pinned HyperFrames 0.8.81. Build before regenerating speech so timeline duration is current.

```powershell
node scripts/build.mjs
python audio/score.py
$env:HYPERFRAMES_BROWSER_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.\film.ps1 check --at '3,12,19.8,22.4,24.5,26,28,29,35.8,38,42,46,54,62,77,85,90,93,101' --strict --json
.\film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic.mp4
node scripts/serve-review.mjs
```

Set HYPERFRAMES_FFMPEG_PATH and HYPERFRAMES_FFPROBE_PATH if necessary. The voice is en-US-AndrewNeural at −3%, with continuous ASAP/GSCM/MCP pronunciation guidance. Word boundaries are cached. Overlong lines fail rather than being accelerated or truncated. The 48 kHz stereo 24-bit master matches timeline duration.

## Scope and fidelity

Interfaces, data, prompts and outcomes are synthetic. The ChatGPT layout was visually referenced in Chrome on 2026-09-29, then recreated in English/light mode with generic content; no account content was copied. Chrome is recreated in code. No native desktop automation was used. asap-portal.com is the requested display address, not a contacted or authenticated site.

The MCP comparison demonstrates the owner's intended workflow. It is not a benchmark or a claim that disconnected AI always fails, MCP guarantees accuracy, or token/time savings have been measured. No app implementation or deployment verification is included. Proposed overnight fixes require review.
