# Natural narration and visible agent inspections — report

- [Plan](test-plan.md)
- Evidence cutoff: 2026-09-29 08:03 UTC
- Baseline: `49f2761c7e9bea154fc625241c6120a983ba1df8`; working changes on `codex/natural-narration`. Final tested commit and CI URL are recorded in the PR before merge.
- Environment: Windows ARM64, Python 3.13.7 x64, Node 24.19, Chrome 153.0.8010.53, HyperFrames 0.8.81, FFmpeg 7.1. Synthetic data/interfaces and narration only.

## Results

| Case | Status | Evidence |
| --- | --- | --- |
| AUD-01 | PASS for automated checks | All 16 generated phrases checked with local faster-whisper 1.2.1/base.en/int8/CPU and synthesis word boundaries. Recognized ASAP/GSCM, two AM, AI, HTML, API, ETL, ChatGPT and MCP. No overlapping/out-of-slot phrases; all post-generation speed factors 1.0. [Complete script, recognition and boundary evidence](../../../../media/metronome-in-sync/audio/pronunciation-review.json). Human listening is not claimed. |
| AUD-02 | PASS | Strict HyperFrames at 6,31,56.8,57.45,57.8,59.5,64,68,72,76.5,106,126: no errors/warnings and zero layout findings; 234 contrast checks. Encoded frames show pointer inside button with press/Creating/complete states; six SVG inspectors progress through source/Flow/dataset lanes, ending with five flags and a healthy marker. [Strict result](../../../../media/metronome-in-sync/renders/narration-agents-check.json), [encoded frames](../../../../media/metronome-in-sync/renders/review/agents-click-samples.jpg). |
| AUD-03 | PASS | HyperFrames rendered 4,320 frames. FFprobe: H.264 1920×1080 30 fps, stereo AAC 48 kHz, 144.000 seconds. Full FFmpeg decode passed. Loudness -16.01 LUFS, true peak -1.90 dBTP. [Measurements](../../../../media/metronome-in-sync/renders/narration-agents-validation.json). |
| AUD-04 | PASS | Two unchanged film-package tests passed, zero failures/errors/skips; four syntax checks passed. Run `20260929T080316586Z-23040-068588a8`, 2026-09-29T08:03:16.588216+00:00 to 2026-09-29T08:03:19.410131+00:00; fingerprint `388eedebb52313370f9b9be4c51de137065081787eda67a79fb1b6f75fa88c82`. Manifest hashes updated for final media. HTTP review/MP4 range checks recorded in the PR. |
| AUD-05 | NOT RUN at cutoff | Required final-head CI pending. Record Merge ready result, run URL and tested SHA in the PR before a head-pinned merge. |

Verifier command:

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

## Resolved findings and limits

The first motion check warned about DOM measurements inside the seek callback. Final animation geometry is fixed and deterministic, with explicit button dimensions and pointer-tip coordinates. Initial scan overlays covered labels; the agents and scan beams were moved above the labels and all layout findings cleared. The first renderer attempt could not locate its managed browser; the final run used installed Chrome through HYPERFRAMES_BROWSER_PATH.

ASR preserves NERP as “and ERP”, METO as “Mito”, Claude as “CLOD” and “your” as “you're”. These are recognition spellings, not conclusive phoneme judgments. A second recognizer returned “build flows” where Whisper inserted a low-confidence (0.27) “field”; the synthesis boundaries also show only build/flows. The owner has a voice-only excerpt for the three requested pronunciation points. Automated recognition and duration checks do not replace human listening or establish that every vowel matches a listener's preference.

The reviewer uses compatible CTranslate2 4.6.0 and PyAV 15.1.0 in workspace scratch; newer downloaded binaries were blocked by Windows Application Control. No system policy was changed. Its model was copied into a shorter workspace path because the native reader could not open the long cache path. ASR models/dependencies are not project dependencies or committed assets. Setuptools emitted its pkg_resources deprecation warning during scratch ASR.

No app feature, test, workflow or access policy changed. This is a film revision. METO/MCP architecture remains illustrative product direction. No native desktop automation was used. Final-head CI evidence belongs in the PR testing section to avoid changing the commit that CI tested.
