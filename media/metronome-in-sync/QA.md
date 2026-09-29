# Narration and agent motion validation

Current cut: **144 seconds, 1920×1080, 30 fps, zero subtitles**, English narration and instrumental music. This revision fixes the Create Flow click, depicts six moving AI agent characters, and replaces dotted-letter speech with continuous delivery using `en-US-AndrewNeural`.

## Evidence

- [Encoded interaction frames](renders/review/agents-click-samples.jpg): hover, press, Creating, completed pipeline, source inspection, Flow inspection, movement, dataset inspection and five flags.
- [Strict check](renders/narration-agents-check.json): 12 sampled times, no lint/runtime/layout/contrast errors or warnings, zero layout findings; 234 contrast checks passed. Automated motion analysis was disabled; before/after motion states were inspected visually.
- [Media measurements](renders/narration-agents-validation.json): full FFmpeg decode passed; 4,320 H.264 frames, stereo AAC 48 kHz, 144.000 seconds; -16.01 LUFS and -1.90 dBTP.
- [All sixteen narration phrases](audio/pronunciation-review.json) were checked with local Whisper base.en and synthesis word boundaries. ASAP/GSCM are spoken continuously, `a.m.` stays one pronunciation unit, and AI/API/HTML/ETL/ChatGPT use normal text. The TTS rate is −3%; all post-generation speed factors are 1.0. The generator now fails on an overlong line instead of accelerating or truncating it.
- Existing film-package tests and four syntax checks passed: `20260929T080316586Z-23040-068588a8`. No test or workflow change.

Speech recognition preserves proper-name spelling ambiguities (NERP, METO, Claude) and cannot establish subjective voice quality. **No human listening pass is claimed.** A second recognizer resolved a low-confidence extra “field” that Whisper inserted before “flows”; the synthesis boundary data has only build/flows.

The film's product-direction, fictional-data and code-recreated Chrome scope remains in [README](README.md). The agent inspection animation implies checks and proposed findings, not autonomous remediation. [Release plan](../../../docs/testing/releases/2026-09-29-narration-agent-motion/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-narration-agent-motion/test-report.md).
