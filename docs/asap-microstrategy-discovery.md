# ASAP MicroStrategy discovery protocol

The catalog scanner's MicroStrategy REST path (`_mstr_*` in
`app/flow_worker.py`) is built against HAR captures of the live ASAP
deployment (MicroStrategy Web 11.5.1200, on-prem, fronted by a custom
Samsung portal). This file records the protocol facts the code depends on,
with their provenance, so future changes are checked against evidence
rather than MicroStrategy defaults.

Provenance tags: **[PROVEN]** observed on the wire in the HAR captures;
**[UNPROVEN]** standard MicroStrategy behavior not yet validated on this
deployment.

## Architecture

One host, three stacked applications:

| Path | Layer | Auth |
|---|---|---|
| `/portal/*` | Samsung custom portal (Spring) | portal session cookie |
| `/lib/api/*` | MicroStrategy Library REST API | `X-MSTR-AuthToken` header |
| `/mstr/*` | MicroStrategy Web legacy servlet | session cookie + rolling state tokens |

The REST API lives under the **custom `/lib` context path**, not the
standard `/MicroStrategyLibrary`. Both paths are named in the portal's
`_COMMON_INFO`; the scanner always uses the custom one. **[PROVEN]**

## Authentication

- The deployment supports **trusted SSO only** (`/lib/api/config/authModes`
  returns mode 64 alone). There is no password login mode; credential-based
  `auth/login` cannot work here. **[PROVEN]**
- The signed-in portal page embeds the working session as inline script
  constants: `_COMMON_INFO` (project id, context paths, iServer name, main
  menu root) and `_SESSION` (the live REST token in `X_MSTR_AUTHTOKEN`, the
  legacy servlet token in `MSTRWEB_AUTH_TOKEN_ENC`). The scanner reads them
  from the already-authenticated browser. **[PROVEN]**
- `_SESSION.MSTRWEB_AUTH_TOKEN` (without `_ENC`) is a **username**, not a
  token. Only the `_ENC` value authenticates `menuInfo.do`. **[PROVEN]**
- `_SESSION` is a credential bundle (tokens, 30-day JWTs, username, client
  IP). Never log it, never persist it, never place tokens in catalog
  entries stored server-side.

## Catalog discovery (what the scanner uses)

- `POST {web}/menuInfo.do?folderId={id}` with form
  `authToken=<legacy>&depth=1|2` returns the portal's own navigation tree
  (`children`/`child` keys both occur; names carry `NN.` ordering prefixes
  the visual menu hides). Menu roots are **role-dependent** — always start
  from this session's `MSTR_MAIN_MENU_ID`, never a hard-coded folder.
  **[PROVEN]**
- `GET {lib}/api/folders/{id}?type=3,55,18&hidden=false` lists a folder.
  `type` is a comma-separated filter. Entries of type 18 are **shortcuts**:
  the executable object is `targetInfo.id` / `targetInfo.type`, not the
  entry's own id. **[PROVEN]**
- Object names look like `P002_050.Regional FOTA^Export Wizard (Sell-out
  Sub)`: a catalog code prefix, the display name, and an export-view label
  after `^`. The scanner strips prefix and suffix for menu-path matching.
- Only type-3 reports are indexed. Documents/dossiers (type 55) use a
  different endpoint family and their export path is unproven.

## Prompts (filters)

- Prompt definitions are **instance-scoped**: `POST
  {lib}/api/reports/{id}/instances` with body `{}` returns `instanceId`
  (dossiers return `mid` instead), then `GET
  …/instances/{iid}/prompts`. **[PROVEN]**
- Member enumeration uses the prompt **`key`** (`{id}@0@10`) as the path
  segment — the bare `id` 404s: `…/prompts/{key}/elements?offset&limit`
  (or `/objects` for OBJECTS prompts). **[PROVEN]**
- Pagination has **no total count and no continuation token**. The only
  terminator is a page shorter than the limit. A run that stops on a full
  page is **truncated, never complete** — the scanner records
  `options_truncated` in the filter's automation and such lists must not be
  treated as authoritative. (The UI itself was observed truncating three
  large prompts at exactly 2200 members.) **[PROVEN]**
- An empty `ELEMENTS` answer means **unfiltered**, not "select nothing".
  **[PROVEN]** This is why prompt enumeration is informational: a full
  extraction never needs the member list.
- `limit=200` is the only observed page size. **[PROVEN]** Larger limits
  are **[UNPROVEN]**.

## Portal DOM anatomy (corroborated by a second, DOM-level analysis)

A separate Playwright/CDP analysis of the live portal corroborated the
scanner's assumptions and supplied concrete selectors:

- Content iframe: `iframe#content-frame` (already the scanner's
  `ASAP_FRAME_SELECTOR`); main navigation in `#header-nav`.
- Loading overlay: `#loading-spinner-container` (already handled).
- Report tabs (export views): `.tabs__item.asap-fn-btn`, `.active` marks
  the current one.
- RUN button: `button.asap-aside-run-btn` — **one hidden copy per loaded
  report tab stays in the DOM (a dozen on multi-tab reports); only the
  active tab's is visible.** Any first-match click can land on a hidden
  button. `_asap_run_control` therefore scans every candidate for the
  first *visible* element, with this class as the primary selector.
- Native `<select>` option values encode the element id the REST API
  uses: `h<DisplayName>;<AttributeGUID>` (matches the element-id format
  in the REST captures).
- Breadcrumb: `.asap-navigation-left .asap-nav-item`.
- Top-level menu folders carry `NN.` ordering prefixes ("02.Mobile")
  that the visual menu hides — the reason for `_mstr_clean_menu_name`.

A full menu-tree snapshot with folder ids exists (2026-08). Folder ids
are **role-dependent per user** - treat any snapshot as a validation
checklist, never as ids to hard-code; the scanner always discovers from
the session's `MSTR_MAIN_MENU_ID`.

### Conflicts between the two analyses

Where the DOM-level analysis disagrees with the HAR captures, **the HAR
evidence wins** - it is hash-verified wire traffic:

- Prompt-answer payload: the HAR shows `PUT …/prompts/answers` takes
  `{"prompts": [{"id", "key", "name", "type", "answers"}]}` with every
  prompt included. The DOM-level analysis describes an
  `{"answers": [{"promptId", …}]}` shape that was never observed on the
  wire; assume it is generic-MicroStrategy folklore until proven here.
- The DOM analysis suggests dossier grid data is fetchable as JSON via
  `GET /lib/api/dossiers/{id}/instances/{iid}?resultFlag=3…`
  **[UNPROVEN]** - not in any capture, but standard for this
  MicroStrategy version and worth probing: it would give clean JSON rows
  for dossier-type (non-Export-Wizard) reports without any DOM work.

## Beyond discovery (not implemented here)

Execution and native CSV export run on the legacy `/mstr` servlet: event
codes (`evt=4001` execute, `5005` poll, `3131`/`3012` export), rolling
`mstrWeb` state tokens that must be scraped from each response, a wait-page
fork on both execution and export, and a final download served as
`application/csv;charset=UnicodeLittle` (**UTF-16LE**) with a non-standard
`total-length` header. The flow runtime keeps using the browser for this.
Worth probing before ever building it: whether this deployment exposes the
standard REST export endpoints (`/lib/api/v2/reports/{id}/instances/{iid}`
with `Accept: text/csv` and friends) — **[UNPROVEN]**, and it would replace
the entire legacy chain if present.
