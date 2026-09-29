# Natural narration, interactive Flows and agent inspections — report

- [Plan](test-plan.md)
- Evidence cutoff: 2026-09-29 08:28 UTC
- Baseline: `49f2761c7e9bea154fc625241c6120a983ba1df8`; working changes on `codex/natural-narration`. Final tested commit and CI URL are recorded in the PR before merge.
- Environment: Windows ARM64, Python 3.13.7 x64, Node 24.19, Chrome 153.0.8010.53, HyperFrames 0.8.81, FFmpeg 7.1. Synthetic data/interfaces and narration only.

## Results

Revision 5 supersedes the pre-feedback v4 at `91afb86b81dbd7daef3b009deaa36e4148e32897`. That historical revision passed run `36540564040`; it is not the final tested revision. The PR records fresh CI at the final v5 head.

Chrome interaction evidence: typed `asap.example`; opened the 12 generic report options; selected Monthly sales report; opened Destination and verified Shared drive, Documents and Local database; chose Local database; selected daily 06:00; confirmed Create Flow enabled only after completion; clicked it and observed Flow created plus Collect → Transform → Load → Repeat. A separate scroll showed the lower four report names. The demo and film share the same event handlers. This is a synthetic demo with no backend creation.

| Case | Status | Evidence |
| --- | --- | --- |
| AUD-01 | PASS for automated checks | All 16 generated phrases checked with local faster-whisper 1.2.1/base.en/int8/CPU and synthesis word boundaries. Recognized ASAP/GSCM, two AM, AI, HTML, API, ETL, ChatGPT and MCP. No overlapping/out-of-slot phrases; all post-generation speed factors 1.0. [Complete script, recognition and boundary evidence](../../../../media/metronome-in-sync/audio/pronunciation-review.json). Human listening is not claimed. |
| AUD-02 | PASS | Strict HyperFrames at 6,31,43.4,46.2,47.8,48.3,49.6,50.6,52.4,53.2,55.45,55.6,57.8,76.5,106,126: no errors/warnings and zero layout findings; 222 contrast checks. Encoded frames show actual menu open/selection states, pointer inside the button and press/release/Creating/complete states. Six SVG inspectors and five issue flags are retained from v4; the final flags state was sampled again. The local dropdown overlay annotations and manual scroll verification are explained below. [Strict result](../../../../media/metronome-in-sync/renders/builder-v5-check.json), [encoded frames](../../../../media/metronome-in-sync/renders/review/builder-interaction-samples.jpg). |
| AUD-03 | PASS | HyperFrames rendered 4,320 frames. FFprobe: H.264 1920×1080 30 fps, stereo AAC 48 kHz, 144.000 seconds. Full FFmpeg decode passed. Loudness -16.09 LUFS, true peak -1.87 dBTP. [Measurements](../../../../media/metronome-in-sync/renders/builder-v5-validation.json). |
| AUD-04 | PASS | Two unchanged film-package tests passed, zero failures/errors/skips; five syntax checks passed. Run `20260929T082839334Z-14240-6ae0b7d6`, 2026-09-29T08:28:39.335883+00:00 to 2026-09-29T08:28:42.087747+00:00; fingerprint `ffcb5ec5d9f738bf10aa85b8fd1445b1901e6159ba7c3f307b61c3b9fa7a1939`. Manifest hashes updated for final media. HTTP review/MP4 range checks recorded in the PR. |
| AUD-05 | NOT RUN at cutoff | Required final-head CI pending. Record Merge ready result, run URL and tested SHA in the PR before a head-pinned merge. |

Verifier command:

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

## Resolved findings and limits

The first motion check warned about DOM measurements inside the seek callback. Final animation geometry is fixed and deterministic, with explicit button dimensions and pointer-tip coordinates. The final v5 click dispatch occurs on release, 120 ms after press. Open menus intentionally cover adjacent field text; local overlap/occlusion markers exist only while that menu is open. Only the four offscreen report options have an intentional-overflow marker because the menu scrolls. Chrome scrolling visibly exposed those four options. There is no global audit suppression. The transparent caret-width text was replaced by a hidden CSS pseudo-element. A CSS inheritance bug in menu layout was corrected. Initial scan overlays covered labels; the agents and scan beams were moved above the labels and all layout findings cleared. The first renderer attempt could not locate its managed browser; the final run used installed Chrome through HYPERFRAMES_BROWSER_PATH.

ASR preserves METO as “Mito”, Claude as “Clod”, occasional “in minutes” as “and minutes”, and a trailing “//” artifact in phrase 3. Recognition spellings are not conclusive phoneme judgments. Updated phrases were regenerated and re-recognized; unchanged phrases 9–11 reuse identical source audio. All phrases have boundary evidence. The opening now avoids “live” entirely. A current voice-only excerpt is supplied. No human listening pass or exhaustive vowel correctness is claimed.

The reviewer uses compatible CTranslate2 4.6.0 and PyAV 15.1.0 in workspace scratch; newer downloaded binaries were blocked by Windows Application Control. No system policy was changed. Its model was copied into a shorter workspace path because the native reader could not open the long cache path. ASR models/dependencies are not project dependencies or committed assets. Setuptools emitted its pkg_resources deprecation warning during scratch ASR.

No app feature, test, workflow or access policy changed. This is a film revision. METO/MCP architecture remains illustrative product direction. No native desktop automation was used. Final-head CI evidence belongs in the PR testing section to avoid changing the commit that CI tested.
