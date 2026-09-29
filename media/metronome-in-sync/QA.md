# Interactive Flow and narration validation

Current cut: **144 seconds, 1920×1080, 30 fps, zero subtitles**, English narration and instrumental music. Revision 5 replaces the form with functional input/dropdown controls, natural typing and press/release animation, and narration emphasizing anyone creating their own flows without code in minutes and connecting their AI tools. Revision 4’s six moving agent characters and continuous English acronym delivery remain.

## Evidence

- [Encoded interaction frames](renders/review/builder-interaction-samples.jpg): typing, report menu and selection, destination menu and selection, schedule menu, press, Creating and completed pipeline.
- [Strict check](renders/builder-v5-check.json): 16 sampled times, no lint/runtime/layout/contrast errors or warnings, zero layout findings; 222 contrast checks passed. Automated motion analysis was disabled; before/after motion states were inspected visually.
- [Media measurements](renders/builder-v5-validation.json): full FFmpeg decode passed; 4,320 H.264 frames, stereo AAC 48 kHz, 144.000 seconds; -16.09 LUFS and -1.87 dBTP.
- [All sixteen narration phrases](audio/pronunciation-review.json) were checked with local Whisper base.en and synthesis word boundaries. ASAP/GSCM are spoken continuously, `a.m.` stays one pronunciation unit, and AI/API/HTML/ETL/ChatGPT use normal text. The TTS rate is −3%; all post-generation speed factors are 1.0. The generator now fails on an overlong line instead of accelerating or truncating it.
- Existing film-package tests and five syntax checks passed: `20260929T082839334Z-14240-6ae0b7d6`. No test or workflow change.

Speech recognition preserves spelling ambiguities (METO/Mito, Claude/Clod, in/and) and a trailing artifact in phrase 3. It cannot establish subjective voice quality. **No human listening pass is claimed.**

The film's product-direction, fictional-data and code-recreated Chrome scope remains in [README](README.md). The agent inspection animation implies checks and proposed findings, not autonomous remediation. [Release plan](../../../docs/testing/releases/2026-09-29-narration-agent-motion/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-narration-agent-motion/test-report.md).
