# Visual Metronome film and MCP ending — test plan

## Scope

Apply the owner's nine-section film feedback. Remove all captions, corner headings and secondary source/hub labels. Show distinct Korean ASAP/GSCM report portals; six named sources; data ready for Excel and the user's AI tools; no-code Flow construction; standalone overnight local AI; five clickable findings with a proposed fix; the METO AI Portal supplied through an API; web-server access; and ChatGPT, Claude and Gemini gaining data capabilities through Metronome MCP.

The new story is 144 seconds to accommodate the added demonstrations. Update the existing package test's explicit duration assertion to that new length; preserve checksums, audio format and link validation. This is a film revision, not implementation of the illustrated application interfaces or integrations. The owner describes the METO/MCP direction; deployment availability is not established by these checks.

## Cases

| ID | Procedure | Expected result |
| --- | --- | --- |
| VIS-01 | Build and run HyperFrames strict checks at 6,18,31,39,47,59,73,81,88,106,117,126,133,140 seconds. | No lint, runtime, layout or contrast issues. Inspect all states, including collapsed/expanded findings and all three AI logos. |
| VIS-02 | Inspect generated markup and encoded frames against the owner's request. | No subtitle/caption overlay, chapter corner, product-overview label or source/hub sublabels. Six sources match the request. Portal tables have Korean period and YoY columns/triangles; no Excel icon in scene 1. Shared-key claim removed. |
| VIS-03 | Inspect narration and audio timing. | Acronyms have explicit spoken spellings (A. S. A. P., G. S. C. M., A. M., etc.); no overlapping phrases, no truncated speech, natural phrase speed where possible; mixed music beneath voice. No subtitle files offered by the current review. |
| VIS-04 | Inspect full encoded MP4 using FFprobe and FFmpeg. | 144 seconds; 1920×1080, H.264, 30 fps; stereo AAC 48 kHz; full decode passes; measured true peak below clipping. Extract encoded review frames after validation. |
| VIS-05 | Open local review in Chrome, play and seek to findings and MCP. Check local links and MP4 byte range. | Revised cut appears without subtitles; chapter buttons work; HTTP 200/206 as appropriate. Only Chrome browser control/code is used. |
| VIS-06 | Run the two existing film-package tests and four syntax checks through the checkout-owned verifier. | Tests/syntax pass, with duration updated for the longer story. All original checksum, WAV-format and media-link checks retained. |
| VIS-07 | Review changes, attribution, source claims and `git diff --check origin/main HEAD`. | No app/workflow changes, private routes, credentials or business data. Fictional `.example` addresses; illustrative proposed fix; official product names and licensed logo assets credited. |
| VIS-08 | Wait for final-head `Merge ready`, record run URL and tested SHA in the PR, then merge pinned to that head. | Required checks pass before merge. |

## Environment

Windows ARM64 host, checkout-owned Python 3.13.7 x64, Node 24, HyperFrames 0.8.81, installed Chrome, FFmpeg/FFprobe. All portal/data fixtures are synthetic. Intermediate captures, TTS cache and render logs stay ignored. No native desktop capture or live-system testing is in scope.
