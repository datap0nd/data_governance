# Visual AI connection — plan

Baseline `a79d924325edbbac3e31a7117ffad9a6c8bfcc2e` (revision 8). [Report](test-report.md).

Replace the AI-access card stack with a visual connection. Three AI logos appear before paths draw into Metronome MCP. Preserve the browser-access panel, narration, timing and prior scenes.

| Case | Procedure | Expected |
| --- | --- | --- |
| LINK-01 | Connected Chrome: inspect AI tools (64.25), Connecting (64.9), Connected (66) using connection-preview.html. Inspect screenshot and console. | Logos precede lines; lines converge cleanly on Metronome; no prompt/status cards, clipping or page errors. |
| LINK-02 | Offline SVG frames from the same module, composed into the revision 8 master with resvg and FFmpeg. Inspect encoded stages and 70-second cut. | Browser and exported diagram agree. Clean composition; no old card remnants. 95 sec, 2850 frames, H.264 1080p30, unchanged AAC stream. |
| LINK-03 | Existing two package tests, six syntax checks, manifest/media checks, HTTP review/range. | Correct hashes/references, passing syntax and successful playback. |
| LINK-04 | Final-head CI; record SHA/run in PR before pinned merge. | Merge ready passes for actual head. |

Browser interaction is restricted to connected Chrome. No native desktop automation or headless browser export/check in this revision. The offline renderer operates on SVG and video files. Audio is copied unchanged; no new synthesis/ASR or listening claim.
