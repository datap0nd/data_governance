# In Sync — frame direction

Plan gate • 1920×1080 composition • 16:9 • scalable to 3840×2160

## Visual thesis

One physical metronome establishes Metronome as the platform. Distinct data windows establish ASAP, GSCM, Big Data Portal and Data Hub as sources. A visible automation pipeline and AI investigation explain how those sources become reports. Flat editorial illustrations show human context. The film should feel calm, precise and comfortable in a small presentation room.

**Current quiet-cut direction after owner review.** No source appears as a metronome. Four foreground data windows identify ASAP, GSCM, Big Data Portal and Data Hub and show the data each contributes. One central Metronome mark connects them. The recording becomes five visible automation stages: Record task, Schedule, Gather, Check and AI ready. AI agents then investigate ASAP and GSCM evidence, producing a sourced insight and report. The fictional portal and every Metronome surface remain in the dark palette.

The presentation keeps a dark Metronome palette throughout. The stage is near-black ink **#07080C**, with very deep blue **#0B1030** in the shadows. One warm-white **#F4EFE6** key light enters from upper left. Haze is soft and directional. Preserve true dark areas so teal feels deliberate, not decorative.

## Palette

| Use | Value | Rule |
|---|---|---|
| Stage | #07080C | Main cinematic background |
| Shadow lift | #0B1030 | Subtle depth, never a bright gradient wash |
| Key light | #F4EFE6 | Soft warm-white edges and practical lights |
| Accent | #5EEAD4 | Metronome teal; selected edges, interaction and core |
| Deep accent | #0D7377 | Low-luminance teal fills |
| Success | #34D399 | Always paired with “Success” |
| Attention | #FBBF24 | Always paired with readable attention or pause text |
| Failure | #F87171 | Always paired with readable failure text if used |

## Type and hierarchy

Use the supplied Outfit font family, weights 300–800. Headline supers use Outfit 800, sentence case and tracking −0.04em. UI uses Outfit 400–600 and tabular numerals. Do not replace a missing font silently; record any fallback in the review notes.

At the 1080p composition size, aim for headline type around 76–92px, supporting labels 26–32px, and hero UI text at least 22–26px wherever the viewer is expected to read it. Keep the end disclosure at least 24px with a quiet background. These are starting layout values, to be checked in the rendered frames, not a reason to alter the exact copy.

Reserve a headline region separate from the busiest scene detail. Use a 96px left/right and 72px top/bottom safe area. Give the longest line a deliberate two-line wrap; never shrink it into a dense paragraph. The S6 “Concept preview” label is small in hierarchy but visible throughout the scene, in the same stable corner above the UI.

The general headline guideline is at most seven words and at least 1.5 seconds. The supplied exact text wins where it conflicts. The kinetic montage intentionally uses 0.5-second words. The current review cut holds the end disclosure for 1.0 second before the final black beat; an earlier 82.0-second start remains a possible gate-2 refinement.

## Product surfaces

Rebuild fictional screens in HTML with these Metronome dark-theme tokens:

| Token | Value |
|---|---|
| Background | #171715 |
| Surface | #201F1C |
| Border | #3D3B37 |
| Text | #E5E3DD |
| Accent | #5EEAD4 |

Use 4px-based spacing, compact tables, fine borders, low decoration and visible status text. Keep row counts and times aligned with tabular numerals. A slight perspective and soft teal edge light can place panels in the scene; the reading surface must remain close enough to front-facing to scan.

The ASAP portal is a neutral fictional browser layout. It must not imitate or expose a real portal. The recording editor and report use product tokens with invented data. The report is HTML, including its line chart, source footer and KPI text; it must not be baked into an AI-generated image.

The “Attention” state visibly explains the stopped action: “Data Hub price feed arrived 38% short. Downstream refresh paused.” No unexplained red or amber icon substitutes for that text. The S6 capability is identified as a **Concept preview** throughout, including the email and audit moments.

## Metronome mark and physical object

The code-drawn mark uses a 24×24 viewBox. This is a supplied vector construction, not a generated brand asset.

| Part | Geometry | Style |
|---|---|---|
| Body | M7.5 21h9L14.6 4.5H9.4L7.5 21Z | Teal fill at 7%; teal stroke 1.6 |
| Arm | M12 6.2l3.2 10.2 | #E5E3DD stroke 1.5; round caps |
| Weight | cx 14, cy 12.4, r 1.7 | Teal fill |
| Base | M6 21h12 | #E5E3DD stroke 1.5 |

The arm and weight pivot at (12, 6.2). The wordmark is “Metronome” in Outfit 800. The single 3D metronome extrudes the same body silhouette, with restrained material roughness and rim highlights. Its arm uses a deterministic swing; the code-drawn mark remains crisp.

Use a Samsung logo only when the owner supplied `brand-inbox/samsung.svg`. Keep it static, original in shape and color, smaller than the Metronome lockup, and separate from the mark. Omit it if absent. The single 3D object appears only in the opening; source systems use data-window UI.

## Motion language

Arrivals use expo or quint out. Pendulums use their sine/cosine phase motion. Linear motion is reserved for clocks and counters. Every shot has a slow camera drift, with motion blur only on fast moves. No bouncy easing.

S4 → S5 → S6 forms one restrained visual progression: recorded actions become visible automation stages, completed runs feed a clear history, and AI agents investigate that evidence before the report emerges. A subtle dark bridge links the sections. The S2 kinetic words remain brief, while the manual-handoff layout stays steady. The silence at 20.0 and the last-beat cut at 83.5 are exact.

Every animation must be seekable from the requested time. Use a fixed seed, absolute time sampling and a single shared timeline. Do not use wall-clock time, uncontrolled random values or a render-dependent physics update.

## Illustration direction

The current cut uses code-drawn flat illustrations rather than photographic stills. A simple city skyline supports the 02:00 automation; people at desks convey planner, finance, sales and logistics roles beside front-facing dark Metronome reports. The source-system sequence uses browser-like data windows and manual-handoff cards. No realistic face, photograph, crystal hall or simulated camera lens appears in the current render.

The earlier generated stills and corner measurements remain archived in the asset ledger as exploration. They do not appear in the current composition. Use the shared dark background, muted teal shapes, warm-white type and minimal details so the product journey stays primary.

## Eight style frames

| Scene | Visual emphasis |
|---|---|
| S1 | Macro physical metronome, rim light and material texture, deep black |
| S2 | Four distinct data windows, connected-source field, controlled headline |
| S3 | One Metronome mark gathering named source windows |
| S4 | Recording steps followed by five visible automation stages |
| S5 | Pipeline run lane and readable history table over a subtle data grid |
| S6 | AI-agent investigation, sourced report and visible Concept preview context |
| S7 | Flat human illustration, front-facing dark report, calm headline |
| S8 | Message, wordmark and disclosure with generous space |

The frame samples are defined by each scene's `styleBeat` in the shared timeline. Their purpose is to approve composition, typography, material and color before motion production.

## Avoid

No glowing brains, robots, circuit boards, binary rain, hologram HUDs, lens-flare spam, emoji, stock gradient blobs or bouncy SaaS motion. The owner specifically requested “pipeline” and “AI agents” on screen; use those plain labels, but avoid ETL, LLM, RPA, SQL, schema and API. Avoid excessive teal glow, overfull UI and numbers that could be mistaken for measured outcomes.
