# Metronome feature inventory

Reviewed on 2026-09-08 against the application navigation and registered APIs at
baseline commit `3ccbf8487b96a73b68d538808b401cc6510a63e8`.
This is a baseline code inventory, not a usage audit. The tables describe what
existed before the Users and refresh-reliability release. The release notes
below identify the changes delivered with it.

The current scope keeps the existing Dashboard unchanged, with
People moved into a dedicated Users section. TMDL Checker retirement, optional
SQL usernames and materialized-view refresh recovery fixes are included in the
[release testing package](testing/releases/2026-09-08-users-refresh-reliability/test-plan.md).
The dashboard concepts were cancelled and removed. Backups remain a
[separate discussion](materialized-view-backup-options.md), not a new feature.

## Baseline navigation (before this release)

There were 20 visible destinations in the baseline navigation. The updated
[navigation](../app/static/index.html) replaces TMDL Checker with Users.
The corresponding page registrations are in
[the frontend router](../app/static/app.js), under `const pages`.

| Location | Current label / route | What currently exists |
| --- | --- | --- |
| Main | Dashboard / `#dashboard` | Report/source counts, active-alert count, last scan, source-health bar, 30-day health trend, team activity, and owner-filtered alert/action tables. |
| Main | Pipelines / `#lineage` | Report selector and graphical lineage; connected upstream systems, files, SQL relations, materialized views, report tables and visuals; refresh planning, pipeline runs and connection explanations. |
| Main | Flows / `#flows` | File, Outlook and website acquisition; recorded and catalog-based flows; schedules, owners, transformations, SQL insertion and downstream materialized-view refresh. Tabs: Flows, Catalog, Run history, Settings. |
| Data | Reports / `#reports` | Report catalog, owners, health/freshness, metadata, usage, documentation, refresh actions and archive controls. |
| Data | Sources / `#sources` | Source catalog, freshness and probe results, ownership, upstream relationships and archive controls. |
| Tools | Create Artifacts / `#create` | **Assets** tab creates a Report, Data Source or Upstream System. **People** tab adds/deletes BI and Business profiles. Also shows manual-entry history. |
| Tools | TMDL Checker / `#bestpractices` | Report best-practice findings, severity/owner filtering and CSV export. Still present and reachable. |
| Tools | Data Quality / `#dataquality` | Read-only row-count, change, null-rate, duplicate-key and value-range checks; configuration, individual/all-check execution and results. Failures currently feed actions/alerts. |
| Tools | Email / `#email` | BI owner email mapping, all-alert scope, alert-summary previews, Outlook drafts/sends and per-person email schedules. |
| Tools | Recurrences / `#recurrences` | Scheduled Outlook delivery from live Power BI table visuals, row rules, grouped recipients, refresh gating, drafts, manual runs and history. |
| Tools | Full Export / `#export` | Selectable Markdown export of report metadata, visuals/fields, DAX, columns, sources, lineage and diagnostics; copy output. |
| System | Changelog / `#changelog` | Product change history. |
| System | Event Log / `#eventlog` | Audit trail of entity changes and actors. |
| System | FAQ / `#faq` | Product guidance; includes descriptions of older alert/checker/People journeys that will need updating when those journeys change. |
| System | Scanner / `#scanner` | Metadata and governance modules, source probes, Power BI connection state, schedules, run histories, stop/recovery controls and failure-notification recipients. |
| System | AI / `#ai` | Runtime/model settings and feature switches for operations investigation, automatic alert review, alert-email analysis, documentation suggestions and Pipeline explanations. |
| System | Updates / `#updates` | Main-update status, checks, update controls and application/runtime information. |
| System | Paths / `#paths` | Flow folder configuration and path validation. |
| System | Flow workers / `#flow-settings` | Shared browser/worker capacity and recording settings; worker start controls. These settings also appear inside Flows. |
| System | Premium Viewers / `#premiumviewers` | Viewer email list used to weight report usage/impact, plus usage synchronization. This is separate from BI/Business profiles. |

The navigation also contains the connected-user identity, application version
and theme toggle; these are controls rather than additional pages.

## Hidden and compatibility surfaces

An absent menu item does not mean the feature or its data has been removed.

### Old page routes and retained renderers

| Surface | Current behavior |
| --- | --- |
| `#alerts`, `#issues`, `#actions`, `#overview`, `#tasks` | All redirect to Dashboard through `pageAliases`. There is no separately routed Alerts, Actions or Tasks page. |
| `#scripts`, `#scheduledtasks`, `#powerautomate`, `#dataimport` | Redirect to Flows. Their old names do not establish separate current products or pages. |
| `#refreshschedule` | Redirects to Scanner. The old standalone `renderRefreshSchedule` implementation remains in the source but is not the routed page. Its pipeline settings APIs still exist. |
| Standalone Alerts / Actions renderers | `renderAlerts`, `renderActions` and `renderActionsContent` remain in the frontend source, outside the current page map. Dashboard still uses alert/action-specific UI and APIs. |
| Standalone Documentation renderer | `renderDocumentation` remains outside the page map; `#documentation` falls back to Dashboard. Documentation is still actively available in Report details, and its API is registered. |
| Older Scanner / Dashboard-alert renderers | `renderScanner` and `renderDashboardAlerts` remain in source; the routed Scanner uses `renderScannerAdmin`, while Dashboard uses its own alert section. |

These facts come from `pages`, `pageAliases`, `navigate` and renderer references
in [app.js](../app/static/app.js). They do not prove that an entire helper section
can be deleted: shared detail controls must be checked separately.

### Registered legacy or alert-adjacent APIs

All of the following routers are still included by
[app/main.py](../app/main.py).

| API family | Retained capability and dependency |
| --- | --- |
| [`/api/alerts`](../app/routers/alerts.py) | List alerts; resolve, reopen and assign them; list owners. Legacy page aliases have not removed these operations. |
| [`/api/actions`](../app/routers/actions.py) | List/update actions and inspect occurrence history. Used by the current Dashboard and incident workflows. |
| [`/api/tasks`](../app/routers/tasks.py) | Task CRUD, board movement, owners and entity links. Task board navigation is gone; the linkable-entity helper is also referenced by documentation code. These are distinct from Flow worker execution tasks. |
| [`/api/email`](../app/routers/email.py) | People email/scope mapping, task summaries, alert summaries and Outlook launch endpoints. Flow/pipeline/recurrence notifications also share email infrastructure. |
| [`/api/email-schedules`](../app/routers/email_schedules.py) | Task-summary schedule/get/update/send-now endpoints and per-person alert-summary schedules. A background dispatcher is still registered. |
| [`/api/best-practices`](../app/routers/best_practices.py) | Checker findings. Checker execution is also called by Scanner governance and the full scan, so removing its navigation alone would leave execution in place. |
| [`/api/schedules`](../app/routers/schedules.py) | Upstream-system schedule data, discrepancy checks, alert trend and source-health trend. The source-health trend is used by the current Dashboard. |
| [`/api/documentation`](../app/routers/documentation.py) | Documentation CRUD/archive, suggestions and AI suggestions. Report details still depend on it; governance also evaluates documentation completeness. |
| [`/api/data-quality`](../app/routers/data_quality.py) | Check configuration/execution/history; source probes also trigger checks and maintain failure actions/alerts. |
| [`/api/ai`](../app/ai/router.py) | Settings, chat, briefing, report risk and operations investigations. Automatic alert enrichment also has a registered background schedule; Pipeline explanations and documentation suggestions are separate features. |
| `/api/admin/refresh-schedule`, `/api/admin/refresh-now` | Hidden-schema compatibility aliases for the current `/api/system/refresh-schedule` and `/api/system/refresh-now` routes. |

### Active services without their own top-level page

These are supporting functions, not retirement candidates merely because they
lack a navigation item:

- [`/api/people`](../app/routers/people.py): BI/Business profiles shared by
  ownership and email; intended to back Users after the move.
- [`/api/lineage`](../app/routers/lineage.py),
  [`/api/pipelines`](../app/routers/pipelines.py) and
  [`/api/pipeline-insights`](../app/routers/pipeline_insights.py): graph data,
  refresh plans/runs/settings, relation samples and explanations.
- [`/api/materialized-views/{source_id}/refresh`](../app/routers/materialized_views.py):
  materialized-view refresh, alongside the post-insertion refresh implemented
  inside Flow execution.
- [`/api/query-history`](../app/routers/query_history.py): report/materialized-view
  query history and comparison.
- [`/api/archive/{entity_type}/{entity_id}`](../app/routers/archive.py): archive and
  restore behavior used in the catalogs.
- Flow recordings, catalog discovery, worker APIs and
  [`/api/flows/worker/.../tasks`](../app/routers/flow_tasks.py): execution support,
  including run tasks and finalization. Do not confuse these with the legacy
  human task board at `/api/tasks`.
- `/flow-runs/{run_id}`: direct navigation to a Flow run; `/api/me` and
  `/api/register`: connected-user identity. These routes are defined in
  [app/main.py](../app/main.py).

## What to keep central

1. **Dashboard, Flows and Pipelines:** the requested operational control panel,
   scheduling/activity, execution results, clear failures and recovery actions.
2. **Users:** existing BI/Business profiles now live here with owner/email editing
   and an optional SQL identity for future permission mapping. People has been
   removed from Create Artifacts. SQL permission grants are not implemented by
   storing that identity.
3. **Reports, Sources and metadata discovery:** they provide the identity,
   dependency and freshness information needed by Pipelines and SQL targets.
   PBIX/TMDL discovery must be distinguished from the TMDL Checker feature.
4. **Runtime administration:** Scanner, Paths, Flow workers, Updates and Event
   Log, plus the applicable authentication and failure-notification services.
5. **Pipeline explanation and refresh services:** retain these when reviewing
   broader AI or governance features; they are directly relevant to the new
   product focus.

## Retirement and consolidation recommendations

The statuses below distinguish the user's request from recommendations. They
are not additional removal authorization.

| Item | Decision status | Recommended scope |
| --- | --- | --- |
| TMDL Checker | **Retired in this release** | Remove its user-facing destination and scanner execution. Preserve PBIX/TMDL metadata extraction, graph discovery and historical findings. Old Checker API requests receive an explicit retirement response. |
| Existing Dashboard | **Keep unchanged; redesign cancelled** | Preserve the existing dashboard behavior and rendering. Remove the experimental dashboard code and preview. |
| People under Create Artifacts | **Moved to Users in this release** | Consolidate profile editing in Users and update owner/email guidance. Retain BI/Business identities and existing owner associations; add optional SQL username metadata. Asset creation stays in Create Artifacts. |
| Legacy standalone page renderers | **Cleanup candidate** | After reference checks, remove obsolete wrappers for unrouted Alerts, Actions, Documentation, Scanner and Refresh Schedule pages. Retain any detail components used by live pages and keep useful compatibility redirects. |
| Alert-summary Email page and per-person alert schedules | **Retirement candidate; owner decision needed** | Strong candidate if recurring alert digests are no longer wanted. Separate profile/email editing from digest delivery before any removal; preserve operational Flow/Pipeline failure mail. |
| Automatic alert AI review and alert-email AI analysis | **Retirement candidate; owner decision needed** | Retire only these feature switches/jobs if their workflows end. Keep operations investigation, Pipeline explanations and any retained documentation assistance separate. |
| Human task board APIs and task-summary schedules | **Retirement candidate; owner decision needed** | Inspect saved tasks, scheduled summaries and documentation dependencies first. Do not remove Flow worker execution tasks. |
| Schedule-discrepancy/documentation-completeness governance | **Review candidate; owner decision needed** | Decide whether these should produce contextual Pipeline information or be retired. Keep schedules and metadata needed to operate Flows/Pipelines. |
| Data Quality | **Review candidate; owner decision needed** | Could become contextual checks in Pipelines. Its checks have a purpose beyond an alerts page; do not delete configured checks merely because alerts leave Dashboard. |
| Premium Viewers | **Review candidate; owner decision needed** | Reassess its usage/impact weighting if alert prioritization is retired. It is not a duplicate of Users. |
| Full Export | **Optional consolidation candidate; owner decision needed** | Consider placing export actions within Reports/Pipelines if a dedicated page adds little value. Retain export functionality until the decision is made. |
| Recurrences | **Keep pending a separate decision** | Its Power BI-to-Outlook delivery workflow is distinct from alert digests and from Flow ingestion. The current request does not authorize its removal. |

Before implementing a candidate retirement, inspect actual saved configuration
and callers, decide whether data should remain readable, and demonstrate any
changed journey in the preview. This inventory used source inspection only; it
does not claim that any feature is unused, that live services were tested, or
that a removal has been deployed.
