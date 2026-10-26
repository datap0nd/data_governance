# Wizard film, revision 13: the real Wizard app — report

[Plan](test-plan.md). Evidence cutoff: 2026-10-26 09:05 UTC. Baseline revision 12, `dd70255`. Final tested SHA and CI URL are recorded in the PR.

Claude Code cloud container: Linux, Node 22.22.0 (HyperFrames' `>=22` floor), Python 3.13.17, HyperFrames 0.8.142 with Playwright's chrome-headless-shell (Chromium 1243) via `HYPERFRAMES_BROWSER_PATH`, FFmpeg 6.1.1. Wizard `main` at `ff4a9e2` (uv, Vite build). Synthetic data only.

| Case | Result | Evidence |
| --- | --- | --- |
| RU-01 | PASS | Each transcript run through Wizard's API. Question 1: GSCM sell-out 925,000 → 1,020,000 (+10.3%), targets 1,000,000 (102%), Samsung share and NERP spend as tabled (MA has no Q3 spend). Question 2: Egypt's Galaxy A37 channel stock 24,092 → 58,675; Saudi Arabia's A57 and Morocco's A17 as stated. Check my data: "6 of 6 claim(s) match the cited evidence", overall CHECKED, and the first answer's badge turns Checked. |
| RU-02 | PASS | Takes `quarter` (80.5 s, 3,320 frames) and `stock` (41.2 s, 1,825 frames) at 3200 × 1800; frames reviewed across both. Real sidebar, composer, systems stage, answer, chart, check panel and Sources drawer. Hidden for the recording: the replay badge, SYNTHETIC badges and notice, the demo suggestions, " · replay mode", the floating "Latest" button. Logged run times: question 1 31.0 s, question 2 25.8 s, check 12.2 s ([markers](../../../../media/metronome-in-sync/capture/takes/quarter/markers.json)). |
| RU-03 | PASS | 12 lines re-timed from `voice-rev12.flac`, all at speed 1; every cut point is digital silence (peak 0.0000 in the 5 ms either side); 34 cues moved with their lines; no overlaps. The speech service (`speech.platform.bing.com`) is blocked from this environment, so no line was re-voiced. |
| RU-04 | PASS | [Encoded samples](../../../../media/metronome-in-sync/renders/review/revision13-samples.jpg) half a second after each cue: GSCM, ASAP, NERP and Attached files lit on "sell-out", "share", "spend", "targets"; headline, table and chart framed on "summary", "numbers", "charts"; six MATCH rows on "checked"; the security card on "safe". Fixed in review: the stage push clipped the top node; the conversation title showed behind the pill; the tag covered the question box. One brief clip remains by design of the app: during the last second of writing, Wizard scrolls its own view and the top node edges out of frame before the camera pulls back. |
| RU-05 | PASS | Hub: `wizard-app-icon.svg` at 9×; end lockup: `wizard-logo.svg` at 10×; both byte-identical to `datap0nd/wizard` `brand/logo` at `ff4a9e2`. No "W" mark remains. |
| RU-06 | PASS | 25 samples: ok, 0 errors, 0 warnings; contrast 75/75 ([check JSON](../../../../media/metronome-in-sync/renders/revision13-check.json)). An earlier run warned that the score was 81.1 s in an 84 s slot: loudnorm's sidechain copy ended after the last word, which would cut the music under the end card. Fixed by padding the voice after loudnorm (also in `score.py`); the score is now exactly 84 × 48,000 samples. |
| RU-07 | PASS | 84.000 s, 2520 frames, H.264 1920×1080 30 fps, AAC 48 kHz stereo; full decode PASS; −16.14 LUFS / −1.97 dBTP ([measurements](../../../../media/metronome-in-sync/renders/revision13-validation.json)). |
| RU-08 | PASS | `tests/test_film_package.py`: 2 passed; syntax checks for remix.py, score.py, screen.py, build.mjs, runtime.js, wizard.js, capture/serve.py, capture/make_transcripts.py pass; `node --check capture/record.cjs` passes. Run ID recorded in the PR for the pushed head. |
| RU-09 | NOT RUN at cutoff | Awaiting owner review of the cut. |
| RU-10 | NOT RUN at cutoff | Final-head CI pending. |

## Method and limits

The app recordings are real UI driven by Playwright in replay mode: Wizard's own tools read its synthetic fixtures, and the transcript supplies the model's turns, written from those tool results. The capture server pauses before each tool call and makes each tool take about as long as a real read, so the stage acts out every step; the timelapse clock shows those recorded durations, not a live Gemini run. No ASR or human listening pass. Rollback: revert this PR's commits to return to revision 12.

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/remix.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/screen.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/compositions/wizard.js --syntax media/metronome-in-sync/capture/serve.py --syntax media/metronome-in-sync/capture/make_transcripts.py
```
