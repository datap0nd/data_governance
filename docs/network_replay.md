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

- **Recipes never cross configurations.** A recipe is keyed by site, report,
  export view, and requested period. A request recorded for one week or one
  export view is never replayed for another; it simply isn't found, and the
  browser flow runs as before.
- **Replayed files are validated like downloads.** The response is checked by
  content, not filename: an HTML sign-in or error page, an empty or truncated
  body, or the wrong file family (text where a workbook is expected, and vice
  versa) rejects the replay. A file that passes still goes through the exact
  same container validation and normalization as a browser download.
- **No credentials are stored.** `Cookie` and `Authorization` headers are
  stripped before a recipe is saved; authentication always comes live from
  the browser profile at replay time. If the profile's session has expired,
  the portal returns its sign-in page, the replay is rejected, and the
  browser flow's existing unattended re-login takes over.
- **Recipes expire.** After 14 days a recipe is dropped and the next run uses
  the browser, refreshing the recipe on success.

## Turning it off

- Whole worker: set the environment variable `DG_FLOW_REPLAY=0`.
- One flow: set `network_replay: false` inside the flow's `downloads`
  configuration.

Both switches disable capture and replay together; runs then behave exactly
as before this feature existed.
