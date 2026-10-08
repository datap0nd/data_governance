# Revision 11 validation

Metronome + Wizard: portals → extract, transform and load → Metronome pipelines into PostgreSQL → a no-code Flow → leader questions across systems → Wizard asks, reads NERP, PostgreSQL and ASAP, answers, rechecks and shows its query → one connected workflow.

- **120 seconds / 2:00**, 1080p30 H.264, 3600 frames; stereo AAC 48 kHz. Full decode PASS. −16.16 LUFS / −1.88 dBTP. [Measurements](renders/revision11-validation.json).
- All 13 narration lines regenerated and fit at speed 1. 26 animation cues found in the synthesizer's word boundaries ([cues](assets/cues.json)); [encoded samples](renders/review/revision11-samples.jpg) taken half a second after each cue show the matching reveal.
- HyperFrames 0.8.142 `check` at 22 times: pass, no errors; contrast 257/257 AA. Two lint warnings remain by design: one draw function measures layout for the chat scroll and pointer, and the composition is a single file. [Check output](renders/revision11-check.json).
- Two package tests and seven syntax checks PASS: `20261008T213635189Z-40304-b4c2fd70`. Review pages, MP4 (HTTP 200) and range requests (206) served locally.

Wizard screens were compared with its replay demo run locally in fixture mode. No ASR or human listening pass this revision. Product readiness was not audited.

[Plan](../../docs/testing/releases/2026-10-09-film-metronome-wizard/test-plan.md) · [Report](../../docs/testing/releases/2026-10-09-film-metronome-wizard/test-report.md).
