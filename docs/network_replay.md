# Network-level export replay

The slowest, most fragile part of an ASAP or GSCM flow run is driving the
portal UI: waiting for overlays, clearing popups, finding the right controls,
and paging virtualized grids. None of that fragility lives in the HTTP request
the portal ultimately sends to produce the export file. The worker now
captures that request once and replays it directly on later runs.

## How it works

1. **Capture.** While the browser performs a normal UI-driven export, a
   context-wide recorder (`app/flow_replay.py`) observes every request,
   response, and download. When the export completes, the request that
   produced the file is identified - by the download's own URL when the
   browser reported one, otherwise by the last response that looked like a
   file export - and stored as a *recipe*: method, URL, filtered headers, and
   body. Recipes are saved in `.export_replay.json` next to the worker's
   browser profile, because they are only valid for that profile's signed-in
   sessions. The run log shows `Recorded the HTTP request behind export N of
   M` when a recipe is stored.

2. **Replay.** The next run of the same export task issues the recorded
   request through the browser context's HTTP client, which shares the
   profile's live SSO cookies. No page renders, no popup needs clearing, no
   element needs finding. The run log shows `replaying the recorded HTTP
   export request instead of driving the portal UI`, and the run's artifact
   carries `export_transport: "http_replay"` (browser exports carry
   `"browser"`).

3. **Fallback.** Any doubt rejects the replay and the run falls back to the
   full browser flow, which re-records a fresh recipe as it succeeds. A
   rejected recipe is forgotten immediately, so a broken one costs a single
   HTTP round trip once - never on every run.

## Safety model

- **Recipes never cross configurations.** Recipe version 3 is keyed by the
  Flow, site, report, the Flow's saved report filters, export view, requested
  period, ASAP semantic download type, Export Report Title, and Export filter
  details. The filters are compared in a canonical form, so the same
  selections saved in a different order still match. A request recorded for
  one Flow, filter selection, week, view, type, or checkbox combination is
  never replayed for another; it simply is not found, and the browser flow
  runs as before. Editing a Flow's report filters therefore makes its next run
  use the browser and record a fresh recipe.
- **Older recipes are re-recorded, not migrated.** Recipes saved before
  version 3 did not carry the Flow or its filters, so they are never found
  again. The first run of each export after the update drives the portal UI
  once and records a version 3 recipe as it succeeds.
- **Replayed files are validated like downloads.** The response is checked by
  content, not filename: a sign-in or error response, an empty or truncated
  body, or the wrong file family rejects the replay. Intentional HTML and Plain
  text preserve `.html` and `.txt` staging suffixes. A file that passes replay
  still goes through the same structural validation, storage, and normalization
  as a browser download.
- **No credentials are stored.** `Cookie` and `Authorization` headers are
  stripped before a recipe is saved; authentication always comes live from
  the browser profile at replay time. If the profile's session has expired,
  the portal returns its sign-in page, the replay is rejected, and the
  browser flow's existing unattended re-login takes over.
- **Recipes expire.** After 14 days a recipe is dropped and the next run uses
  the browser, refreshing the recipe on success. The number of days is the
  **Reuse a saved export request for** setting.

## Turning it off

- Every Flow: switch off **Repeat the portal's own export request when
  possible** under **System > Settings > Flows**. The value is copied into
  each run when it is queued, so runs already queued keep what they had. The
  environment variable `DG_FLOW_REPLAY=0` still works as the starting value
  until the setting is saved, and for a job that carries no value.
- One flow: set `network_replay: false` inside the flow's `downloads`
  configuration.

Both switches disable capture and replay together; runs then behave exactly
as before this feature existed.
