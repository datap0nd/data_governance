# Why Metronome exists — plan

Baseline `cc50a1388feeb76f3420ecc52fa80085f39265d4` (revision 9). [Report](test-report.md).

Clarify the opening: interactive portals supply report downloads; useful analysis/scheduled reporting still requires extract, transform and load; Metronome lets people build pipelines without code and schedule, trigger and track them in one place. Add In testing in the top-right throughout overnight agents and findings. Preserve accepted later scenes and move them 15 seconds later.

| Case | Procedure | Expected |
| --- | --- | --- |
| WHY-01 | Inspect connected Chrome and encoded opening: portals, ETL, pipeline capabilities. Review script and new voice recognition/boundaries. | Clear problem → required work → product purpose. Extract/standardize/load and four management controls reveal on spoken cues. Acronyms recognized. No accelerated/truncated speech. |
| WHY-02 | Inspect agents/findings and badge boundary times. | In testing present for all 52–72 sec, in top-right without covering browser controls; absent outside. |
| WHY-03 | Probe/decode export; inspect cuts at 9/20/31/52/64/72/85/102. Verify narration slots/offsets, closing cues, manifest, HTTP and existing package tests plus seven syntax checks. | 110 sec / 3300 frames / H.264 1080p30 / stereo AAC 48 kHz. Later voice/cache unchanged except timeline offset. No blank gaps or caption reintroduction. |
| WHY-04 | Final-head CI; record tested SHA/run in PR. | Merge ready passes before head-pinned merge. |

Connected Chrome is the only browser-control surface. SVG/video/audio files are composed offline with resvg/FFmpeg. No live portals, native desktop automation or deployment validation. No human listening claim.
