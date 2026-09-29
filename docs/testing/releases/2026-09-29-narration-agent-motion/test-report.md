# Metronome revisions 4–6 — test report

[Plan](test-plan.md). Evidence cutoff: 2026-09-29 09:00 UTC. Base main: `49f2761c7e9bea154fc625241c6120a983ba1df8`; working branch `codex/natural-narration`. Final tested head and CI run are recorded in PR #149 before merge.

Historical attribution: v4 head `91afb86b81dbd7daef3b009deaa36e4148e32897` passed [CI 36540564040](https://github.com/datap0nd/data_governance/actions/runs/36540564040). The owner then requested interactive menus; v5 head `1192c3b3346b7e6006cfac7dcf2ae51ce290b772` passed [CI 36543155581](https://github.com/datap0nd/data_governance/actions/runs/36543155581). Neither is the final v6 head. The owner's subsequent pacing, parallel coding, agent movement, dashboard and MCP comparison feedback is included in v6 and requires fresh final-head CI.

Environment: Windows ARM64 host, x64 Python 3.13.7, Node 24.19, Chrome 153.0.8010.53, HyperFrames 0.8.81, FFmpeg 7.1. Synthetic media only; no native desktop automation or live-system action.

| Case | Status | Evidence |
| --- | --- | --- |
| FILM-01 | PASS for automated checks | All 13 final phrases recognized with local Whisper base.en and aligned synthesis boundaries. Every phrase fits its slot at speed factor 1.0. No overlap/truncation. Gap time 63.067 → 31.154 sec, reduction 50.6%. [Recognition](../../../../media/metronome-in-sync/audio/pronunciation-review.json). No human listening pass claimed. |
| FILM-02 | PASS | 19 strict samples, zero remaining errors/warnings/info findings; 248 contrast checks. Visual review confirms simultaneous code/Flow, independent cross-report agents, dashboard without AI Portal, two access modes and ChatGPT comparison/table. [Strict output](../../../../media/metronome-in-sync/renders/revision6-check.json), [encoded frames](../../../../media/metronome-in-sync/renders/review/revision6-samples.jpg). |
| FILM-03 | PASS | 104.000 seconds; 3,120 H.264 frames at 1920×1080/30fps; stereo AAC 48 kHz. Full decode passed. -16.19 LUFS; -1.95 dBTP. [Measurements](../../../../media/metronome-in-sync/renders/revision6-validation.json). |
| FILM-04 | PASS | Two package tests, zero failures/errors/skips; five syntax checks. Run `20260929T090032959Z-30724-74bb9ae8`, 2026-09-29T09:00:32.961417+00:00 to 2026-09-29T09:00:35.781673+00:00; fingerprint `915d438b55129be037e0b7df0f521b3c45ff104b62d9b92237ad35b9cc9d4616`. Manifest hashes match. HTTP review/demo/range evidence is recorded in PR. |
| FILM-05 | NOT RUN at cutoff | Fresh final-head CI pending; record successful Merge ready URL and exact tested head in PR before head-pinned merge. |

## Commands and browser evidence

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
film.ps1 check --at '3,12,19.8,22.4,24.5,26,28,29,35.8,38,42,46,54,62,77,85,90,93,101' --strict --json
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v6.mp4 --quiet
```

V5 Chrome interaction verified the shared form handlers: type a website, select one of 12 reports, choose among Shared drive/Documents/Local database, choose a schedule, confirm enabled Create Flow and click to create the illustrated pipeline. Scrolling exposed the remaining four report options. V6 retains those handlers, changes the typed address to asap-portal.com, scales the film frame and accelerates the event timeline. Its menu/button states were sampled again.

## Resolved findings and limitations

The initial v6 audio sentence exceeded its slot and the generator rejected it; its wording was shortened and regenerated at unchanged speed. An early check read the audio file while it was being rewritten; checks were repeated after synthesis completed. Line-number contrast was corrected. Inspectors initially moved outside individual row containers; they now share the fleet parent and its coordinate space. No global layout suppression or test weakening was introduced. Only the existing intentional dropdown layering/scroll annotations remain.

Whisper preserves “in minutes” as “and minutes”, “and a proposed fix” as “in a proposed fix”, and a trailing “//” artifact in phrase 3. These are recognition differences, not conclusive phoneme judgments. No human listening or exhaustive vowel correctness is claimed. Scratch ASR dependency compatibility choices and its pkg_resources warning do not alter project dependencies or OS policy.

The ChatGPT shell was observed in Chrome on 2026-09-29 and recreated in English/light mode with generic content. No account content is embedded. The before/after workflow, errors, successful result and time/token comparison are illustrative product direction, not measured performance, universal disconnected-AI failure or a guarantee that MCP ensures accurate data. No actual portal or MCP calls were made. The requested asap-portal.com text is display-only. No app, workflow or access policy changed.
