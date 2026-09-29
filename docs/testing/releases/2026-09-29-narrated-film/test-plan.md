# Narrated Metronome film — test plan

## Scope

Replace the 84-second musical-metaphor film with a narrated data-platform presentation. Cover left-to-right source ingestion and organized datasets, light Chrome and Excel visuals, overnight agents, evidence, a sourced dashboard, instrumental background music and Korean subtitles. Changes are confined to the film and its documentation.

## Cases

| ID | Procedure | Expected evidence |
| --- | --- | --- |
| FILM-01 | Build with `node scripts/build.mjs`; run HyperFrames strict checks at 3, 17, 25, 35, 51, 59, 70 and 81 seconds. | No lint, runtime, layout or contrast errors or warnings. Inspect all eight scene frames. |
| FILM-02 | Render the full composition; inspect the encoded MP4 using FFprobe and FFmpeg. | 84 seconds, 1920×1080, 30 fps, H.264 video, AAC audio; decode succeeds; narration and music present with no clipped audio. |
| FILM-03 | Inspect the English/Korean script, SRT files, font coverage and encoded subtitle samples. | 14 ordered non-overlapping cues within 0–84 seconds; Korean glyph coverage; subtitles outside application windows. |
| FILM-04 | Inspect rendered story scenes against the owner request. | Static Metronome icon; inputs left, organized database right; Chrome controls; recognizable Excel ribbon and worksheet; light UIs; substantial overnight audit and findings sequence; no musical-theme animation. |
| FILM-05 | Open the review page in Chrome, play and use scene buttons. Fetch subtitle links and MP4 byte ranges. | Playback starts; chapter seeking works; correct Korean-captioned cut appears; review links and downloads resolve. |
| FILM-06 | Run `python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs --syntax media/metronome-in-sync/audio/score.py`. | Existing media checksum, WAV-format and local review-link tests pass; syntax passes. |
| FILM-07 | Run `git diff --check`; review asset sources and changed-file list. | No whitespace errors, credentials, private domains or unrequested application changes. Music attribution retained; interfaces and figures identified as illustrative. |
| FILM-08 | Wait for the final PR head's required `Merge ready` check and record its run URL and SHA in the PR. | Head-pinned merge only after the gate succeeds. |

## Environment and cleanup

Windows ARM64 host; use a checkout-owned Python 3.13 x64 environment to match locked binary wheels, Node.js 24, installed Chrome, HyperFrames 0.8.81, FFmpeg and FFprobe. The rendering and browser inspection use code and Chrome only. Intermediate reference downloads, speech caches, tool installations and rendered temporary files remain outside tracked delivery files or under ignored paths. The checked-in MP4, source, soundtrack and eight frames remain available after cloning.
