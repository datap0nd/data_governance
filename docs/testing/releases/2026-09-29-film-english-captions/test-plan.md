# English film captions — plan

Baseline revision 10: `97f0abf3b060e9d7292fbc35b329cde3c097fa74`. [Report](test-report.md).

Add English captions for all narration, retain original words/acronyms, and avoid covering UI. Keep narration/music and 110-second timing unchanged. Supply burned-in MP4 plus SRT/VTT sidecars and matching editable HTML.

| Case | Procedure | Expected |
| --- | --- | --- |
| CAP-01 | Compare cue text with full narration; verify synthesis boundaries minus PCM trim, ordered intervals and pauses. | Full coverage, readable phrase breaks, no overlapping cues or captions during long silent gaps. |
| CAP-02 | Inspect encoded opening/builder/agents/closing and connected Chrome. | Readable English in bottom strip; complete UI retained above it; In testing still visible. |
| CAP-03 | Probe/decode MP4, compare copied AAC packet hashes, run existing film package tests and changed-source syntax checks. | 110 sec, 3300 frames, 1080p30; exact original audio; matching manifest/media links. |
| CAP-04 | Final-head required CI; record SHA/run in PR. | Merge ready passes before head-pinned merge. |

Browser interaction uses connected Chrome only. Offline FFmpeg/libass composes files. No new speech synthesis or product/deployment validation.
