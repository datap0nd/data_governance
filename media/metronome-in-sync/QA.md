# Visual film validation — 29 September 2026

The [current MP4](renders/review/animatic.mp4) is a newly rendered **144-second film with no subtitles**. It is 1920×1080, H.264 at 30 fps (4,320 frames), with stereo AAC at 48 kHz. HyperFrames 0.8.81 used Chrome 153.0.8010.53. Full FFmpeg decoding passed. Encoded loudness measured −16.16 LUFS, −1.92 dBTP and 14.90 LU range. Music remains under narration and continues through visual reading pauses.

## Visual and narrative checks

The [14 encoded samples](renders/review/encoded-samples.jpg) cover both Korean report portals, all six source names, the centered icon/name-only hub, Excel and AI tools, code-to-no-code transition, input selection and pipeline creation, standalone overnight local AI, five findings before/after expansion, METO portal with an external API connection, internal-server/browser access and all three MCP boost states.

No subtitle, chapter-corner or product-overview element remains. Scene 1 has no Excel icon/window. The shared-key claim is removed. Korean glyph coverage for portal text is complete. The current review offers no subtitle downloads.

The [strict check](renders/revision-check.json) reports zero lint, runtime, layout and contrast errors/warnings: 14 layout sample times and 197 contrast checks across five sampled times. The motion-analysis feature was not enabled; animation states were inspected visually. The [encoded measurements](renders/encoded-validation.json) record media, narration and glyph checks. All 16 voice phrases fit at natural speed, with explicit acronym spellings in `narration.json`; no phrase overlaps or truncation was detected.

## Playback and package

Chrome chapter navigation sought to 78 seconds and visibly played the expanded finding, then sought to 122 seconds and played the ChatGPT boost. Encoded samples separately confirm the Claude and Gemini states. The review, frame page, script and ninth scene image returned HTTP 200. The MP4 range request returned 206 (`bytes 0-1023/11967978`).

The two existing film-package tests and four applicable syntax checks passed in run `20260929T071406561Z-18540-8790388e`. The test's duration contract was updated from 84 to 144 seconds for the expanded story; all checksum, WAV-format and link checks remain intact.

Chrome is recreated in code and Excel reuses the owner's Scribble ribbon. Data, issue fixes and addresses are illustrative. The METO/MCP integration story follows the owner's product direction and is not a deployment verification. Proposed fixes remain subject to human review. Source and license information is in the [ledger](assets/LEDGER.md).

See the [release plan](../../../docs/testing/releases/2026-09-29-visual-film-mcp/test-plan.md) and [report](../../../docs/testing/releases/2026-09-29-visual-film-mcp/test-report.md). Final-head CI evidence is recorded in the PR before merging.
