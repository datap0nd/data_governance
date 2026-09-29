# Natural narration and visible agent inspections — test plan

Baseline: `49f2761c7e9bea154fc625241c6120a983ba1df8`. Scope: the owner's narration correction, Create Flow click alignment and animated overnight agents. Film only; no app or workflow changes. [Report](test-report.md).

## Cases

| ID | Procedure | Expected result |
| --- | --- | --- |
| AUD-01 | Regenerate all 16 sentences with English Andrew, continuous acronym delivery and ordinary AI/API/HTML/ETL/ChatGPT text. Compare the generated speech with local Whisper base.en; inspect synthesis word boundaries for ASAP/GSCM/AM/MCP. | Key acronyms and two AM are intelligible in automated recognition. No punctuation-induced full stops inside initialisms, no speed-up, truncation or overlap. Preserve raw recognition differences; automated recognition is not a human listening pass. |
| AUD-02 | Run strict HyperFrames at 6,31,56.8,57.45,57.8,59.5,64,68,72,76.5,106,126 seconds. Inspect before/press/creating/complete states and agent travel. | Pointer tip stays inside the actual button, press precedes creation, and the changing label does not move the target. Six agent characters visit source, Flow and dataset stages; five flags and one healthy marker complete the sequence. No label occlusion, subtitles, layout/runtime/contrast errors or warnings. |
| AUD-03 | Render 144 seconds at 1080p/30 fps. Decode the encoded MP4, inspect sampled frames, measure loudness/true peak and verify audio duration. | 4,320 H.264 frames, stereo AAC 48 kHz, full decode succeeds, no clipped peak, final mixed audio matches the new narration. |
| AUD-04 | Run existing film package tests plus syntax for audio, build, runtime and review server. Verify manifest hashes and review HTTP/range responses. | Two unchanged package tests pass, syntax passes, current playable file and accurate hashes. |
| AUD-05 | Review diff and final-head CI; pin merge to the tested SHA. | No native desktop automation, app changes, credentials or test/workflow weakening. Required Merge ready succeeds before merge. |

## Commands

```text
python audio/score.py
node scripts/build.mjs
film.ps1 check --at '6,31,56.8,57.45,57.8,59.5,64,68,72,76.5,106,126' --strict --json
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v4.mp4 --quiet
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

Windows ARM64 host, x64 Python 3.13.7, Node 24.19, Chrome 153.0.8010.53 and HyperFrames 0.8.81. Speech analysis uses synthetic narration only. Character SVGs are original code assets. Inspection markers are illustrative and never imply automatic remediation. Audio cache, ASR models and intermediate renders remain ignored or outside the repository.
