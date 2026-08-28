# Metronome Production Hardening and Platform Evolution Plan

**Status:** Draft v0.2 for senior engineering review

**Date:** 2026-08-27

**Scope:** Security, correctness, durability, architecture, operations, API/UI quality, testing, and release engineering

**Planning assumption:** One or two senior engineers, with identity/infrastructure support when required. Estimates are directional, not commitments.

## 1. Purpose

Metronome has evolved from a read-only governance panel into a production automation control plane. It now launches Outlook, drives authenticated browsers, stores credentials and tokens, schedules work, writes Flow outputs to PostgreSQL, and executes transform programs on Windows workers.

This plan describes how to make that system safe and supportable without a full rewrite. It deliberately separates immediate containment from architectural evolution so urgent risk reduction does not wait for a platform migration.

This document supersedes the production-architecture assumptions in [`plan.md`](../plan.md), particularly the statements that Metronome never writes to production databases and is not an ETL tool. The historical document should be relabeled or reconciled before the production-readiness gate.

## 2. Executive decision

The recommended approach is:

1. Treat the current deployment as a **single-node Windows appliance** while it is contained and hardened.
2. Establish real user and worker trust boundaries before broader network exposure.
3. Fix known false-success, ambiguous-commit, token-storage, and backup defects.
4. Introduce one durable operation, occurrence, lease, and outbox model on the single node.
5. Separate the FastAPI control plane from authenticated Windows execution agents.
6. Move operational state to a dedicated PostgreSQL control database only when multi-process availability or concurrency is required.
7. Preserve the existing Flow and Pipeline safety patterns; do not perform a big-bang rewrite or premature microservice split.

No new production side-effect category should be added until Milestone 2 is complete. Network-wide access remains blocked until Milestones 1A and 1B pass the explicit Milestone 1C re-exposure gate. Even after that gate, restart-sensitive scheduled effects remain limited to approved workflows until Milestone 2.

## 3. Outcomes and success measures

The program is complete when Metronome can demonstrate all of the following:

- Every human and worker action has an authenticated, immutable principal.
- Authorization is deny-by-default and enforced by the server for every registered route except an explicit public allowlist such as liveness.
- Arbitrary uploaded programs cannot execute under the Metronome service or interactive user.
- Every long-running action has a durable operation ID. Before agent separation, restart survival means durable status plus safe recovery or explicit `unknown`; after Milestone 3, agent execution can continue independently of an API restart.
- Every scheduled occurrence is materialized once and has at most one valid execution lease at a time, even if execution remains at-least-once.
- Every external effect has an idempotency key and a truthful terminal state, including an explicit `unknown` state.
- A successful database mutation cannot be reported as an ordinary failure without also reporting its commit ambiguity.
- Backups are online-consistent, bounded, observable, off-host, and proven by restoration.
- A failed application or schema upgrade can return to a known usable release while releases remain schema-compatible. After irreversible or externally visible new-version writes, recovery uses an explicitly rehearsed forward-repair or disaster-recovery procedure rather than a casual rollback claim.
- Operators can diagnose delayed, failed, or unknown work without direct database surgery.
- The production API contract is covered at HTTP level and the main operator journeys are covered in a real browser.

Recovery, retention, availability and alerting objectives must be approved before the Milestone 1C production re-exposure decision. They may be tightened again before Milestone 4:

| Measure | Proposed starting target | Final owner decision |
|---|---:|---|
| Control-plane availability | 99.5% monthly | Product/operations |
| Scheduled occurrence dispatch lateness | 95% within 2 minutes | Product/operations |
| Backup RPO | Proposed: 1 hour for SQLite and 15 minutes after PostgreSQL migration; must be no longer than the accepted external-effect reconciliation window | Business owner |
| Restore RTO | 4 hours | Business owner |
| Unknown external-effect outcomes | Alert within 5 minutes | Operations |
| Worker heartbeat loss | Alert within 2 missed intervals | Operations |
| Critical security remediation | Before network re-exposure | Security owner |

The proposed values are review prompts, not approved commitments. Restoring an older control database can forget already-executed external effects; every restore therefore enters quarantine with schedules and claims paused until activity after the backup high-water mark is reconciled.

## 4. Current risks that drive the plan

| ID | Risk | Evidence | Required outcome |
|---|---|---|---|
| R-01 | Application access is a no-op and every caller is admin. | [`app/local_access.py`](../app/local_access.py#L79-L86), [`app/main.py`](../app/main.py#L186-L199) | TLS, authenticated identity, route-level RBAC, immutable audit actor |
| R-02 | The server listens on plaintext `0.0.0.0`; credentials and privileged operations cross that boundary. | [`setup.ps1`](../setup.ps1#L364-L382), [`app/routers/flows.py`](../app/routers/flows.py#L1445-L1455) | Immediate containment, then authenticated TLS |
| R-03 | Unauthenticated `.py`, `.ps1`, and `.exe` uploads can execute with inherited environment secrets and potentially elevated task rights. | [`app/routers/flows.py`](../app/routers/flows.py#L2044-L2067), [`app/flow_worker.py`](../app/flow_worker.py#L4596-L4635), [`setup.ps1`](../setup.ps1#L430-L438) | Signed/allowlisted transforms in a least-privilege execution boundary |
| R-04 | Workers self-identify and can claim or forge work without credentials. | [`app/routers/flows.py`](../app/routers/flows.py#L3486-L3642) | Authenticated worker identities and per-job lease tokens |
| R-05 | Scheduled email and recurrence runs say `sent` after task launch rather than receipt reconciliation. | [`app/routers/email_schedules.py`](../app/routers/email_schedules.py#L599-L622), [`app/routers/recurrences.py`](../app/routers/recurrences.py#L1191-L1221) | One durable delivery outbox with `pending`, `submitted`, `failed`, and `unknown` |
| R-06 | SQLite uses WAL, but backup copies only the main file and can omit committed pages. | [`app/database.py`](../app/database.py#L1391-L1397), [`app/scanner/runner.py`](../app/scanner/runner.py#L56-L72) | SQLite online backup, verification, rotation, off-host copy, restore drill |
| R-07 | DPAPI failure stores Power BI tokens as base64 plaintext. | [`app/scanner/pbi_auth.py`](../app/scanner/pbi_auth.py#L164-L173) | Fail-closed protected storage and rotation of affected tokens |
| R-08 | Schedulers, locks, futures, and cancellation are process-local. | [`app/main.py`](../app/main.py#L374-L507), [`app/routers/recurrences.py`](../app/routers/recurrences.py#L63-L64), [`app/routers/pipelines.py`](../app/routers/pipelines.py#L58-L61) | Enforced singleton first; durable jobs, occurrences, and leases next |
| R-09 | Vercel uses ephemeral state while app startup launches background Windows-oriented services. | [`api/index.py`](../api/index.py#L12-L17), [`vercel.json`](../vercel.json#L26-L29) | Remove production Vercel target or make it explicitly read-only |
| R-10 | The frontend accumulates global handlers and sometimes reports success without checking HTTP status. | [`app/static/app.js`](../app/static/app.js#L3574-L3589), [`app/static/app.js`](../app/static/app.js#L4417-L4457) | Immediate regression fixes, then one API client and explicit page lifecycle |
| R-11 | Production HTTP, browser, migration, and restore boundaries are weakly tested. | [`.github/workflows/tests.yml`](../.github/workflows/tests.yml#L20-L40) | API integration, worker contract, browser, migration, recovery, and failure-injection coverage |

## 5. Guiding invariants

These are architectural constraints, not implementation suggestions:

1. **Default deny:** absence of an authorization declaration must deny a route; only a reviewed public allowlist may bypass authentication.
2. **Identity is not a display name:** people, workers, reports, schedules, and revisions use immutable IDs.
3. **Launch is not success:** starting a task, browser, SQL transaction, or Outlook call is never a terminal success.
4. **Unknown is a valid terminal condition:** uncertain external effects must not be replayed automatically.
5. **At-least-once plus idempotency:** do not claim exactly-once behavior across Outlook, browsers, filesystems, or external databases.
6. **Immutable execution input:** a job executes a frozen revision and payload, not mutable live configuration.
7. **One occurrence, one key:** each scheduled due time has a durable unique occurrence record.
8. **Leases are scoped and fenced:** worker identity, opaque lease token and monotonically increasing attempt/fencing token are required for state updates. Lease expiry alone cannot make a non-fenceable external effect safe to replay.
9. **Secrets are explicit:** child processes receive an allowlisted environment; failures in protected storage fail closed.
10. **Recovery is part of delivery:** backup, restore, migration, rollback, and unknown-outcome handling are acceptance criteria.
11. **Routers translate; services decide:** HTTP routers must not own core scheduling, persistence, or execution policy.
12. **Compatibility is temporary:** adapters may support old contracts for one release, with a declared removal milestone.

## 6. Target architecture

```text
Browser / operator
      │ TLS + Entra/OIDC + RBAC
      ▼
FastAPI control plane
      ├── application services
      ├── schedule occurrence materializer
      ├── job and lease service
      ├── delivery outbox / receipt reconciler
      ├── operation status API / SSE
      ├── configuration revision service
      └── audit, retention, health, and metrics
      │
      ▼
Dedicated operational database
      ├── SQLite while deliberately single-node
      └── PostgreSQL before multi-instance availability
      │ outbound authenticated polling
      ▼
Windows execution agents
      ├── Outlook / COM
      ├── Power BI and browser adapters
      ├── approved filesystem roots
      └── explicitly scoped source/target database adapters
```

PostgreSQL itself can provide conditional claims, leases, advisory locks, and `SKIP LOCKED`; Kafka, RabbitMQ, Redis, Kubernetes, and a fleet of microservices are not prerequisites.

### Core durable concepts

The minimal final-form schema for principals, operations, occurrences, outbox and migration history is introduced by the Milestone 1 prerequisite. Milestone 2 expands it into the full durable job model. The combined model must represent:

- `operations`: user-visible command, actor, status, timestamps, correlation ID, remediation.
- `schedule_occurrences`: schedule, intended local time, UTC due time, unique occurrence key, policy and outcome.
- `jobs`: immutable payload snapshot, required capabilities, idempotency key and current lease.
- `job_attempts`: worker, lease, heartbeat, timing, failure classification and terminal receipt.
- `delivery_outbox`: origin operation/occurrence, payload hash, dispatch token hash and submission state.
- `worker_identities`: credential hash/certificate, capabilities, enabled/revoked state and last heartbeat.
- `configuration_revisions`: immutable draft/published revisions, author, diff, validation result and active pointer.
- `schema_migrations`: ordered version, checksum, applied timestamp and release identity.

## 7. Decisions required before implementation branches diverge

| Decision | Recommended default | Required by | Reviewer notes |
|---|---|---|---|
| D-01 Supported topology during hardening | One Uvicorn/NSSM app process plus local workers | Milestone 0 | |
| D-02 Human authentication | Entra ID/OIDC validated by FastAPI, or a cryptographically validated proxy assertion over loopback/mTLS with client identity headers stripped | Milestone 1 | |
| D-03 Role model | `viewer`, `operator`, `designer`, `credential_admin`, `platform_admin`; role-only enforcement in M1 unless a complete approval workflow is separately approved | Milestone 1 | |
| D-04 Worker authentication | Per-worker certificate or rotating secret plus opaque per-claim lease token | Milestone 1 | |
| D-05 Transform policy | No arbitrary web uploads; reviewed, versioned, signed/hash-allowlisted registry | Milestone 0 | |
| D-06 Outlook integration | Keep COM initially with durable receipts; separately assess Microsoft Graph | Milestone 1 | |
| D-07 Operational database | Stabilize durable model on SQLite; migrate to dedicated PostgreSQL before multiple API replicas | Milestone 4 | |
| D-08 Release target | Windows service is production; Vercel is removed or replaced by a separate read-only entrypoint with no lifecycle, mutation, credentials or local state | Milestone 0 | |
| D-09 Frontend evolution | Incremental ES modules/TypeScript and Vite; no framework rewrite | Milestone 2 | |
| D-10 Recovery requirements | Approve RPO, RTO, audit retention, artifact retention and break-glass process | Milestone 1C | |

## 8. Delivery strategy and critical path

The milestones are gates, not simply calendar phases. Workstreams can run in parallel, but no gate may be skipped.

```text
M0 Containment + characterization
            │
            ▼
M1P Versioned migration baseline
 ├── M1A Trust boundary ───────────┐
 └── M1B Correctness/recovery ─────┤
                                   ▼
                         M1C Controlled re-exposure gate
                                   │
                                   ▼
                         M2 Durable single-node model
                                   │
                                   ▼
                         M3 Windows agent separation
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
          M4 PostgreSQL / multi-instance   Skip M4 if not required
                    │                             │
                    └──────────────┬──────────────┘
                                   ▼
                         M5 Production operations gate

Characterization, containment and restore checks begin before the first M0 deployment. API/UI contracts, broader tests, observability, and release engineering begin in M1
and progress alongside the critical path.
```

### Directional schedule

Assuming one or two senior engineers with timely identity/infrastructure help:

| Delivery state | Cumulative elapsed range | What it permits |
|---|---:|---|
| Contained appliance (M0) | Under 1 week | Existing work on a restricted host/network only |
| Trusted and truthful single node (M1P + M1A + M1B + M1C) | 3–7 weeks | Controlled multi-user use for explicitly accepted workflows; restart-sensitive schedules remain constrained until M2 |
| Durable single node (M2) | 7–14 weeks | Restart-safe scheduling and consistent operation semantics |
| Separated execution agents (M3) | 11–22 weeks | Safer Windows automation and independent control-plane lifecycle |
| Multi-instance control plane (optional M4) | 15–30 weeks | HA/concurrency only if the business requirement is approved |

The ranges are intentionally broad. The senior reviewer should replace them after choosing identity, transform, Outlook, database, and staffing decisions. M1A and M1B are parallel; API/UI, test, observability, and release work must also be staffed throughout rather than deferred to the end.

## 9. Milestone 0 — Immediate containment

**Objective:** Reduce the current blast radius without waiting for the final identity or architecture design.

**Indicative effort:** 3–7 engineering days.

**Entry condition:** Current production appliance and affected integrations have been inventoried.

### Work

- **M0-00 Characterize before containment deployment**
  - Add HTTP checks for route reachability, the temporary maintenance policy, worker claim denial, transform-upload denial and critical read-only journeys.
  - Automate backup integrity and clean-copy startup validation before modifying production reachability.
- **M0-01 Network containment**
  - Bind Uvicorn to loopback unless it is behind the approved TLS/authentication front door.
  - Add Windows Firewall rules limiting direct port 8000 access.
  - Inventory every legitimate caller before changing reachability.
- **M0-02 Dangerous endpoint containment**
  - Install one server-side maintenance policy that denies every mutation and external-effect route by default, including Flow/Pipeline execution and SQL work, transform upload, update, credential enrollment, email, open-path and worker control.
  - Maintain a short, explicit, tested temporary allowlist for essential local workflows; audit every permitted invocation.
- **M0-03 Transform containment**
  - Stop accepting executable uploads.
  - Inventory existing transform paths, owners, hashes, Flow dependencies and required permissions.
  - Quarantine unknown or unowned transforms pending review.
- **M0-04 Secret incident check**
  - Scan Power BI caches for `"format": "plain"` without logging token material.
  - Inventory every credential reachable by the web and worker accounts: Power BI, database, email, browser profiles, API, proxy, update, service-account and environment secrets.
  - Revoke/rotate material that may have been exposed; verify old credentials fail and record the incident decision without copying secrets into the report.
- **M0-05 Recoverable pre-change snapshot**
  - Take a stopped-service or online-API SQLite backup that includes all committed state.
  - Run `PRAGMA quick_check`, record size/hash/timestamp, and copy it off-host.
- **M0-06 Deployment declaration**
  - Enforce one supported app process with a real startup interlock such as a Windows named mutex or held database lock, not documentation alone.
  - Remove/disable Vercel, or provide a different read-only composition root that cannot load migrations, schedulers, workers, credentials, mutable local state or side-effect routers.
  - Add a production-warning section to README and installation docs.

### Exit criteria

- An unapproved LAN host cannot reach the application control API.
- Automated route-inventory checks prove the maintenance policy blocks every unallowlisted mutation and external effect.
- Executable transforms cannot be introduced through the web API.
- Privileged local endpoints are enumerated, logged and locally constrained.
- Any plaintext Power BI tokens have been rotated.
- Every credential plausibly exposed to web/worker execution has a documented risk decision; rotated credentials are proven invalid and new child-process environments contain no unrelated secrets.
- A verified backup exists off the host; a clean-copy service start, schema/count/hash check and controlled smoke test pass.
- A two-process test proves the second scheduler/app instance fails closed before starting background work.
- Any retained read-only Vercel composition exposes only its tested read route allowlist and has no startup side effects.

### Rollback rule

Do not roll back to unauthenticated network exposure or executable uploads. The safe fallback is localhost-only operation with production-changing features paused.

## 9A. Milestone 1 prerequisite — Versioned migration and recovery baseline

**Objective:** Prevent Milestone 1 security/correctness schema changes from adding more versionless startup DDL.

**Indicative effort:** 3–7 engineering days; must finish before any Milestone 1 schema-changing release.

### Work

- Introduce ordered, checksummed migrations and a `schema_migrations` record.
- Establish a baseline version for existing production snapshots without replaying destructive work.
- Refuse startup when schema is newer than the application, a checksum has drifted, or a required migration is partially applied.
- Add the minimal final-form tables needed by Milestone 1: principals/role mapping, worker credentials, `operations_v1`, `schedule_occurrences_v1`, SQL mutation idempotency ledger and `delivery_outbox_v1`.
- Define expand/contract and point-of-no-return rules before the first migration.
- Create named, sanitized production-like snapshots and document their row counts, sizes and required validation hashes.

### Exit criteria

- Upgrade from each named snapshot succeeds and an idempotent rerun changes nothing.
- Checksum drift and incompatible/newer schema both block startup with actionable diagnostics.
- An injected mid-migration failure leaves the prior release/database usable or invokes the rehearsed recovery procedure.
- Clean-host restore identifies which configuration is restored, rebuilt, reissued or deliberately reauthorized.
- No Milestone 1 feature introduces ad hoc startup DDL outside the migration runner.

## 10. Milestone 1A — Production trust boundary

**Objective:** Authenticate humans and workers, enforce authorization, and establish least privilege.

**Indicative effort:** 2–4 weeks, dependent on identity and certificate support.

### Work

- **M1A-01 Identity integration**
  - Integrate Entra ID/OIDC or IIS Windows authentication.
  - Validate issuer, audience, signature, expiry and nonce/state.
  - Map immutable external subject/object ID to a local principal record.
  - If a proxy supplies identity, isolate the backend on loopback/mTLS/firewall, strip client-supplied identity headers, and make FastAPI validate a signed assertion or trust only the pinned authenticated proxy channel.
- **M1A-02 Authorization matrix**
  - Inventory every registered route, including reads, downloads, SSE/status, diagnostics, audit and artifacts, and assign a policy/minimum role.
  - Add centralized dependencies/policies with deny-by-default behavior.
  - In Milestone 1, use explicit elevated roles for credentials, send, SQL writes, deletes, full refreshes and updates. Do not claim an approval workflow unless its request, approver separation, expiry, revalidation, audit, UI and tests are separately designed and approved.
- **M1A-03 Audit identity**
  - Remove client-controlled names and IP/client keys as identity.
  - Record authenticated principal ID, delegated actor if any, request ID, action, target, before/after revision and result.
- **M1A-04 Web boundary**
  - Terminate TLS and forbid credential submission over HTTP.
  - Add Trusted Host/origin validation, security headers, safe content policy, body/rate limits, and CSRF protection if cookies are used.
  - Bundle and pin runtime JavaScript currently loaded from remote CDNs where tokens share the execution context.
  - Define secure behavior for IdP outage, signing-key rotation, disabled/deprovisioned users, group changes, token expiry, logout and session invalidation.
- **M1A-05 Worker authentication**
  - Create per-worker credentials with rotation and revocation.
  - Bind claim, heartbeat, progress, artifact and completion calls to both worker identity and a random lease token.
  - Validate trusted capability labels; workers cannot self-grant capabilities.
  - Accept Outlook and other effect receipts only through the authenticated agent channel or with an independently protected signature bound to worker, attempt, immutable payload and source occurrence.
- **M1A-06 Least privilege**
  - Split interactive browser/Outlook access from SQL-write and web-service privileges where feasible.
  - Pass child processes a minimal environment allowlist.
  - Restrict approved paths, reject arbitrary UNC open-path calls, and add adapter-specific URL/redirect allowlists.
  - Remove `RunLevel Highest` and RDP console-restoration dependencies where the automation permits.
  - Install transforms only through a signed release into an ACL-protected immutable registry; immediately before execution, copy/verify the exact approved bytes and reject UNC, traversal, symlink, junction and reparse-point escapes.
  - Avoid shell interpolation and enforce time, memory, process-tree, filesystem and network limits; terminate descendants as well as the parent process.
- **M1A-07 Break-glass design**
  - Define a named-owner, local-only recovery procedure that does not mean “turn authentication off.”
  - Use time-limited or one-use material, rate limiting, audit/alerting, automatic expiry and post-use rotation.
  - Store and drill recovery material according to the agreed ownership process.

### Exit criteria

- Anonymous requests receive `401`; authenticated users with insufficient roles receive `403`.
- Every registered route has an explicit policy and authorization test; only the reviewed public liveness allowlist bypasses authentication.
- Direct backend access, forged identity/role headers, wrong issuer/audience, expired tokens and unknown signing keys all fail closed.
- Disabled/deprovisioned users and revoked/changed roles lose access within the approved propagation window.
- Audit records use immutable authenticated principal IDs.
- Credentials cannot be enrolled over plaintext HTTP.
- Worker ID alone cannot register, claim, update or finish work.
- A revoked worker is rejected without restarting the application.
- Unknown transforms and child-process access to unrelated secrets are blocked.
- TOCTOU replacement, traversal, UNC/reparse escape, child-process escape and inherited-secret tests pass.
- The break-glass drill alerts the owner, expires automatically and cannot create remote anonymous administration.
- One operator completes a canary workflow through the authenticated front door.

### Rollout and rollback

Authentication is enforced before any shadow period. Only RBAC decisions may run in shadow/audit mode while Milestone 0 containment remains active. Show proposed roles to users, resolve unmapped legitimate operators, then enforce. Rollback may pause operations or use the break-glass procedure; it must not restore anonymous admin access. Compatibility flags must never re-enable anonymous access, executable upload, plaintext enrollment or false-success behavior, and secure-default combinations are tested.

## 11. Milestone 1B — Correctness and recovery blockers

**Objective:** Remove known false-success and ambiguous-state defects before expanding production use.

**Indicative effort:** 1–3 weeks and can run in parallel with Milestone 1A.

### Work

- **M1B-01 PostgreSQL write outcome truth**
  - Make Flow SQL handoffs distinguish pre-commit failure, rolled-back failure, committed success and unknown commit outcome.
  - Add a SQL mutation operation and immutable idempotency key.
  - For every retryable mutation, write a uniquely constrained authoritative ledger in the same target PostgreSQL transaction as append or managed snapshot.
  - If a target/mode cannot share that transaction, classify disconnect or lost-response outcomes as `unknown`, advertise the action as non-auto-retryable, and require reconciliation rather than claiming idempotency.
- **M1B-02 Truthful Outlook state**
  - Move recurrences, email schedules, generic sends and pipelines onto one dispatch/outbox service.
  - Use `queued -> launched -> draft_created | submitted_to_outlook | failed | partial_unknown | unknown` and persist per-message identity/evidence.
  - Link every dispatch to its source operation and the minimal final-form schedule occurrence introduced by the migration prerequisite.
  - Store a dispatch-token hash; validate the authenticated agent/attempt, token, mode, immutable payload hash, per-message identity and expected/submitted counts.
  - Persist partial evidence, but make partial/mismatched outcomes non-replayable pending operator reconciliation.
  - Keep pipeline/Flow execution outcome and notification outcome orthogonal and visible; notification failure must not rewrite an otherwise successful data operation.
  - Rename UI semantics from delivered/sent to submitted where appropriate.
  - Backfill old `sent` records as `legacy_unverified` rather than receipt-confirmed.
- **M1B-03 Backup implementation**
  - Replace raw file copy with `sqlite3.Connection.backup()` or coordinated `VACUUM INTO`.
  - Write to a temporary file, verify integrity, atomically rename, checksum, rotate, and replicate off-host.
  - Persist backup result and alert on failure, age and disk growth.
  - Run backups frequently enough to remain inside the approved RPO with safety margin.
  - Define the full recovery set: database, TLS/OIDC configuration, service configuration, worker credentials/certificates, transform registry, token material, browser/Outlook profiles and required filesystem roots. For DPAPI-bound material, explicitly choose re-enrollment, reissuance, escrowed key/certificate recovery or a portable vault.
  - After any restore, keep scheduling and claims quarantined, record the backup high-water mark, reconcile later external effects and reopen only through an audited decision.
- **M1B-04 Protected token storage**
  - Make DPAPI/credential-store failure block token persistence.
  - Use restrictive ACLs and atomic file replacement.
  - Expose a sanitized unhealthy/auth-required status rather than silently degrading.
- **M1B-05 Frontend truth fixes**
  - Guard and clean up global document listeners.
  - Make every mutation check HTTP status before showing success.
  - Fix object-detail error decoding so pipeline blockers and remediation are visible.
  - Poll the exact launched Power BI/scanner attempt, not unrelated historical state.
- **M1B-06 Side-effect boundary restrictions**
  - Restrict open-path to configured local roots.
  - Reject unapproved private, loopback, link-local or redirected portal destinations.
  - Redact secrets and proxy userinfo from diagnostics and tracebacks.
- **M1B-07 Identity containment and review queue**
  - Disable ambiguous name-based routing and automatic archival when canonical identity is not unique.
  - Add an admin API/UI queue for ambiguous people, ownership and Power BI report matches; record resolution actor, evidence and chosen canonical ID.
  - Pause affected schedules/Flows by default until ambiguity is resolved.
- **M1B-08 In-flight cutover plan**
  - Define a drain/freeze boundary for running Flows, pipelines, scans, queued Outlook tasks/receipts, due schedules and queued SQL handoffs before switching to the new operation/outbox statuses.
  - Map each active legacy record deterministically, reconcile orphan receipts, preserve `next_run_at`, and document rollback treatment for every mapped state.

### Exit criteria

- Successful append and managed-snapshot Flow SQL handoffs return success and an operation/commit state.
- For transactionally supported modes, reusing a SQL mutation idempotency key cannot apply the same mutation twice; unsupported/ambiguous modes become `unknown` and block automatic retry.
- Disconnect tests before commit, after commit/before response and across restart/retry produce the documented commit state without unsafe replay.
- A forced Outlook failure never records receipt-confirmed submission. Pipeline/data execution and notification statuses remain separate.
- Tokenless, forged, stale, cross-attempt and mismatched Outlook receipts cannot produce success. Partial item evidence is retained as `partial_unknown` and is not auto-replayed.
- A clean host restores or deliberately rebuilds/reissues every item in the recovery-set inventory, then passes integrity, schema, count/hash and controlled read/write smoke tests.
- DPAPI failure cannot create a plaintext token cache.
- Re-entering a page cannot multiply destructive handlers.
- No frontend mutation reports success after a non-2xx response.
- IPv4/IPv6 loopback, private/link-local, encoded-address, DNS-rebinding, userinfo, redirect, traversal, UNC, symlink, junction/reparse, case-variation and path-replacement adversarial tests pass.
- All ambiguous identity records are either resolved with audit evidence or their dependent automation remains paused.

## 11A. Milestone 1C — Controlled production re-exposure gate

**Objective:** Make the decision to permit authenticated multi-user access explicit and evidence-based. This gate always applies; completing code in M1A/M1B is not enough.

### Required evidence

- Milestone 0, migration prerequisite, M1A and M1B exit criteria are green.
- Authentication is enforced; only RBAC decisions—not authentication—have completed shadow/canary validation.
- Approved RPO, RTO, retention, break-glass and restart-risk decisions are signed by the accountable owners.
- Named HTTP characterization and route-policy tests cover all registered routes, including sensitive reads/downloads/diagnostics.
- Clean-host recovery and restore-quarantine/reconciliation drills pass.
- The production environment installs from the reviewed locked dependency set, and the M1 release is staged with a schema-compatible fallback or declared forward-repair boundary.
- Sanitized liveness/startup/traffic/degraded health, structured request/action logs and alerts for backup failure, disk pressure, stale receipts, identity failure and second-scheduler startup are operational.
- Critical authenticated journeys—login/logout, role denial, Flow/Pipeline error recovery, send/receipt state and destructive confirmation—pass keyboard-only and have zero critical/serious axe violations.
- Backup failure, stale receipt, expired identity and second-scheduler startup alert/drill reach the accountable owner within the approved threshold.
- Accepted workflows and residual restrictions are listed. Restart-sensitive scheduled effects remain paused or explicitly risk-accepted until Milestone 2.
- The release point-of-no-return and code/schema compatibility window are documented.

### Decision

The product, security and operations owners jointly record `approve`, `approve with listed restrictions`, or `do not approve`. Re-exposure is canary-first through the authenticated front door; direct backend access remains blocked. A failed gate leaves the appliance in Milestone 0 containment.

## 12. Milestone 2 — Durable single-node operation model

**Objective:** Make scheduling and long-running work restart-safe before separating processes.

**Indicative effort:** 4–8 weeks.

### Work

- **M2-01 Complete migration ownership**
  - Extend the Milestone 1 migration baseline to all existing schema/data changes.
  - Remove startup replay of the giant best-effort SQL list and any request-path schema repair.
  - Continue testing upgrades from every named production-like snapshot.
- **M2-02 Application-service boundaries**
  - Create services/repositories for operations, scheduling, jobs, notifications, configuration and identity.
  - Make routers translate HTTP only; schedulers call services rather than router internals.
- **M2-03 Durable operations**
  - Standardize operation status such as `queued`, `running`, `cancel_requested`, `waiting_receipt`, `succeeded`, `failed`, `cancelled`, `too_late`, and `unknown`.
  - Return `202 Accepted` plus operation ID for scans, syncs, data-quality work, Flows, pipelines and sends.
  - Expose status/events by polling first; add SSE only if it materially improves operator experience.
  - Return server-calculated `allowed_actions`; UI cancel/retry/resume controls must follow effect-specific state rules and never offer retry for an unreconciled unknown effect.
- **M2-04 Occurrence materialization**
  - Create a unique occurrence for each `(schedule_id, due_at)` before external work.
  - Define retry, catch-up, coalesce, skip, blackout and missed-run policies.
  - Record even skipped or resource-conflicted occurrences.
- **M2-05 Jobs, attempts and leases**
  - Store immutable job payloads and required capabilities.
  - Claim transactionally with lease owner/expiry, heartbeat, monotonically increasing attempt/fencing token and bounded retry.
  - Reject stale progress and terminal writes after reassignment.
  - Fence SQL/file effects with target-side ledgers, unique keys or atomic promotion; immediately revalidate the lease before effect start.
  - Use Windows Job Objects or equivalent local process-tree control to prevent orphan execution where possible. If a non-fenceable effect may continue after lease loss, mark it `unknown` and do not reassign automatically.
- **M2-06 External-effect idempotency**
  - Assign immutable keys to email submissions, SQL loads, file promotions and other effects.
  - Preserve `unknown` after a lost commit boundary; require reconciliation or operator decision before replay.
- **M2-07 Scheduler leadership**
  - Add a database-backed leader lease so two app processes cannot materialize schedules concurrently.
  - Continue to support only one process until multi-process tests and PostgreSQL control state are complete.
- **M2-08 Canonical time model**
  - Store event instants as UTC.
  - Store schedule wall time with IANA timezone and explicit DST/month-end policy.
  - Centralize next-occurrence calculation for Flows, email schedules and recurrences.
  - During migration, record the former host timezone, preview old/new next occurrences, pause ambiguous changes, require operator confirmation and preserve the prior `next_run_at` for rollback analysis.
- **M2-09 Canonical identities**
  - Add immutable foreign keys for people/ownership and canonical Power BI tenant/workspace/report identity.
  - Backfill alongside existing names through the audited M1B review queue; affected automation remains paused until resolved.
- **M2-10 Immutable execution snapshots and scoped configuration revisions**
  - Make the critical-path guarantee a frozen, hashed job snapshot for every execution.
  - For Flows and other domains that need change governance, add draft, validate/dry-run, publish, active pointer, diff, optimistic concurrency and rollback.
  - Migrate validated Flows into published revision 1. Flows with missing/quarantined transforms or unresolved identity/path dependencies become blocked drafts/inactive and require explicit review.
  - Pin schedules and jobs to a published revision where that domain supports revisions; do not block unrelated durable-operation work on a universal revision subsystem.

### Exit criteria

- Concurrent scheduler ticks create one occurrence.
- Killing and restarting the API preserves durable status and safely recovers, reclaims or explicitly marks every job `unknown`; uninterrupted execution is not promised until agent separation.
- Duplicate requests are deduplicated before a known handoff/commit. After an unknown boundary, the system blocks automatic replay and exposes reconciliation instead of claiming exactly-once effects.
- A stale or partitioned worker cannot update job state after reassignment; non-fenceable continuing effects produce `unknown` rather than a concurrent replacement.
- Every long operation is reload-safe and exposes a durable status.
- DST gap/fold, month-end, host-timezone change and missed-run tests pass.
- Schedule migration shows before/after occurrences, pauses ambiguities and preserves prior values for rollback analysis.
- Safe existing Flows retain equivalent published behavior as revision 1; unsafe/quarantined Flows are visibly blocked rather than silently activated.
- Migration from every named snapshot and its documented rollback/forward-repair rehearsal pass against the traceability matrix and fixed hardware/time budgets.

## 13. Milestone 3 — Separate Windows execution agents

**Objective:** Decouple the web/control process from Windows-specific side effects while retaining those necessary integrations.

**Indicative effort:** 4–8 weeks after Milestone 2 primitives exist.

### Work

- **M3-01 Agent protocol**
  - Use outbound authenticated polling initially.
  - Define claim, heartbeat, progress, cancellation, artifact and terminal-receipt contracts.
  - Version the protocol and keep a one-release compatibility window.
- **M3-02 Capability routing**
  - Express trusted capabilities such as Outlook profile, adapter, site, network root, execution mode and database role.
  - Match job requirements transactionally; never trust worker-self-declared privilege.
- **M3-03 Execution isolation**
  - Separate Outlook/browser, filesystem transform and SQL-write execution where feasible.
  - Use dedicated low-privilege accounts/processes, explicit environment variables and filesystem roots.
  - Keep reviewed browser profiles and secrets outside job payloads.
- **M3-04 Adapter extraction**
  - Move COM/browser/filesystem/database execution behind adapter interfaces.
  - No API router may directly invoke Outlook, a browser, Explorer or a long database mutation.
- **M3-05 Local-agent compatibility**
  - Preserve an easy single-machine install by running an authenticated local agent.
  - Local mode must use the same durable protocol and security checks as remote agents.
- **M3-06 Failure and drain behavior**
  - Support worker disable, revoke, drain and upgrade.
  - Recover expired leases according to effect-specific replay policy.
  - Never auto-retry an unknown external effect.
  - Test a partitioned worker that continues running past lease expiry; stale state writes are fenced and non-fenceable effects are not concurrently reassigned.

### Exit criteria

- Restarting the API does not terminate active agent work or lose its durable status.
- A revoked or wrong-capability agent cannot claim a job.
- Loss of an agent produces bounded lease recovery or an explicit unknown outcome.
- Transform processes cannot read unrelated application secrets.
- No API router directly performs COM/browser automation.
- Each migrated capability cohort has its own canary and rollback evidence: Outlook receipt loss/partial submission, SQL unknown commit, browser-profile loss, filesystem permission/atomic-promotion behavior and transform isolation.
- Artifact comparison uses a documented semantic canonicalizer when exports contain nondeterministic metadata; raw checksums are used only where byte identity is expected.

### Rollout and rollback

Shadow the new agent with non-side-effecting or draft work, then canary one low-risk Flow group. Pause claims during rollback. Rollback may return jobs to the previous authenticated local agent only when their effect status is known; uncertain work remains paused for reconciliation.

## 14. Milestone 4 — PostgreSQL control plane and multi-instance readiness

**Objective:** Enable controlled API/scheduler availability and higher write concurrency only if the product requires it.

**Indicative effort:** 4–8 weeks, gated by an explicit business need and Milestone 3 completion.

### Work

- **M4-01 Dedicated operational PostgreSQL**
  - Do not reuse an arbitrary business/source database.
  - Port the versioned durable schema and transactional claims.
  - Use row locks, unique keys, advisory locks and `SKIP LOCKED` where appropriate.
- **M4-02 Migration rehearsal**
  - Restore a production copy, migrate, and validate counts, foreign keys, payload hashes, occurrence keys, pending jobs and outbox state.
  - Preserve old SQLite read-only for audit and rollback analysis.
- **M4-03 Cutover**
  - Pause occurrence materialization and new claims.
  - Drain or explicitly classify active leases.
  - Take and verify the final SQLite backup, migrate, validate, switch configuration, then release canary agents.
- **M4-04 Multi-instance verification**
  - Run at least two stateless API instances and one elected/materializing scheduler path.
  - Test concurrency, process death, network partitions, lease expiry and database failover behavior.
- **M4-05 PostgreSQL recovery**
  - Configure encrypted backups/PITR, retention, monitoring and restoration against the approved RPO/RTO.

### Exit criteria

- Multiple API processes do not duplicate schedule occurrences or terminal writes.
- Claims remain correct during process loss and network interruption.
- PITR/restore meets approved RPO/RTO.
- Retention prevents unbounded operational growth.
- The cutover and rollback boundary is documented and rehearsed.

### Rollback boundary

A configuration rollback is straightforward only before PostgreSQL accepts new mutations. After that point, prefer forward repair unless a tested reverse-delta procedure exists. Preserve idempotency and occurrence keys across cutover so retries cannot resend or reapply effects.

## 15. Cross-cutting workstream — API and frontend

This work begins in Milestone 1 and continues alongside the architecture path.

- **UI-01 Standard API contract**
  - Define one error shape: `code`, `message`, `field_errors`, `blockers`, `remediation`, `operation_id`, `request_id`.
  - Add Pydantic response models to every state-changing route and every sensitive/production-critical read.
  - Support legacy error decoding for one release, then remove it.
- **UI-02 One API client**
  - Centralize authentication, CSRF, correlation IDs, JSON/error decoding, cancellation and timeout behavior.
  - Prohibit raw `fetch` outside the client through linting.
- **UI-03 Page lifecycle**
  - Give every page explicit bind/unbind ownership for listeners, timers and outstanding requests.
  - Extract router/lifecycle, table, dialog, toast, status and operation primitives.
- **UI-04 Incremental module split**
  - Split Flows, Pipelines, Recurrences and Scanner first.
  - Introduce ES modules/TypeScript and a small build pipeline only after behavior tests protect the seams.
  - Preserve routes, terminology and visual design unless semantics are currently inaccurate.
- **UI-05 Operation experience**
  - Display pending/running/waiting/unknown states and remediation.
  - Support reload-safe history, correlation IDs and effect-specific cancel/retry/resume controls driven only by server-advertised `allowed_actions`.
- **UI-06 Accessibility**
  - Standardize dialogs on the existing good focus-trap pattern.
  - Fix sortable headers, labels, clickable rows, focus restoration, navigation announcement and keyboard-only behavior.
  - Require zero critical/serious axe violations in golden journeys.
- **UI-07 Scale**
  - Add cursor pagination and server-side filter/sort to large catalogs and event histories.
  - Add list virtualization only where measured browser performance warrants it.
- **UI-08 Stored-content safety**
  - Prefer `textContent`/default escaping.
  - Sanitize only deliberately supported rich-text fields.

## 16. Cross-cutting workstream — observability and operations

- **OPS-01 Health endpoints:** define sanitized liveness, startup readiness, traffic readiness and dependency/degraded health separately. Migration incompatibility may block startup; stale backup, worker unavailability, outbox age and non-leader status usually degrade/alert without causing restart loops. Probes never expose secrets.
- **OPS-02 Structured telemetry:** structured logs with timestamp, severity, request ID, operation ID, run/job/dispatch ID, actor ID and duration.
- **OPS-03 Metrics and alerts:** queue age, dispatch lateness, lease expiry, worker heartbeat, unknown outcomes, receipt age, SQL commit ambiguity, backup age/failure, DB/WAL/disk size and last successful critical Flow.
- **OPS-04 Retention:** define detailed-event, summary, artifact, audit, dispatch, scan and backup retention. Archive before deletion when policy requires it.
- **OPS-05 Runbooks:** authentication outage, stuck scheduler, lost worker, unknown SQL outcome, unknown Outlook submission, failed migration, restore, secret rotation and disk exhaustion.
- **OPS-06 SLO ownership:** assign service, data, security and business owners plus escalation paths.
- **OPS-07 Alert drills:** inject backup failure, disk pressure, stale outbox, missed heartbeat, unknown effect, expired identity and migration failure; verify the correct page reaches the accountable owner within the approved threshold.

## 17. Cross-cutting workstream — release engineering

- **REL-01 Reproducible dependencies:** create a fully pinned, hash-locked dependency set and make CI install exactly it.
- **REL-02 Supply-chain controls:** dependency/secret scanning, SBOM, verified bundled executables, pinned browser/runtime assets and signed release artifacts.
- **REL-03 Versioned releases:** build immutable version directories, run preflight/migrations/smoke tests, switch code/config atomically, and retain the previous release. Code rollback is permitted only while both versions are schema-compatible and before irreversible/new-version external writes.
- **REL-04 Expand/contract migrations:** keep schema changes backward-compatible across the rollback window; avoid destructive changes in the same release that introduces replacements.
- **REL-05 Release health gate:** require startup/traffic readiness, migration, backup and critical-journey checks before marking the release active. Snapshot restore after email or SQL effects is disaster recovery with mandatory reconciliation, not routine application rollback.
- **REL-06 Supported environment matrix:** test supported Python versions and Windows deployment; Linux remains useful for portable unit/API tests but does not prove Outlook/browser behavior.

## 17A. Milestone 5 — Production operations gate

**Objective:** Prove the chosen architecture is supportable in production. This gate always applies after the selected deployment target: after Milestone 3 for a durable single-control-plane deployment, or after Milestone 4 if multi-instance operation is required.

### Entry criteria

- All milestones required by the chosen supported topology have owner sign-off.
- No accepted residual risk is undocumented, ownerless or missing an expiry/review date.

### Exit criteria

- Threat model, complete route-policy inventory and worker trust model are reviewed.
- Locked production dependencies, SBOM, signed/verified release inputs and environment matrix pass.
- Named state-transition, security, browser/accessibility, recovery, failure-injection and load fixtures pass on the declared hardware profile.
- Clean-host recovery, restore quarantine/reconciliation, schema migration and the applicable code rollback/forward-repair path are rehearsed.
- Backup, disk, stale outbox, missed heartbeat, unknown effect, expired identity and migration-failure alert drills reach the accountable owner within target.
- On-call follows runbooks to diagnose and safely recover a stuck and an unknown job without direct database edits.
- Retention and capacity dashboards prove control data, WAL/logs, artifacts and backups remain bounded.
- One full canary scheduling cycle completes for each supported effect cohort; no false success, unsafe replay or orphaned operation remains.
- README, installation, architecture, security and historical planning documentation match deployed behavior.

### Decision

Product/service, security and operations owners jointly record final approval for the explicitly named topology and workflow set. Any later workflow that introduces a new credential, external effect, worker capability or recovery boundary must repeat the relevant threat, idempotency, test and runbook gates.

## 18. Verification strategy

### Required layers

| Layer | Required coverage |
|---|---|
| Unit | Authorization policies, status reducers, error decoding, schedule/DST logic, idempotency, configuration diffs, redaction |
| HTTP/API | Route-inventory policy mapping plus success, validation, 401, 403, conflict, dependency failure, idempotency and standard error bodies for mutations and sensitive reads |
| Database integration | Real Flow SQL transaction behavior, migration snapshots, unique occurrence keys, claims, leases and retention |
| Worker contract | Registration/authentication, capability matching, claim/fencing token, heartbeat, revoke, stolen/replayed/cross-job token, lease expiry, reassignment race, stale completion and forged updates |
| Outlook integration | Draft, submitted, partial, failed, forged, tokenless, stale/cross-attempt/duplicate, timeout, process-loss boundaries and unknown receipt paths |
| Browser E2E | Authentication/roles; create/edit/delete; Flow draft/publish/run/cancel/resume; pipeline blocker recovery; scanner reload; email pending/submitted/unknown |
| Accessibility | Keyboard-only journeys, focus order/traps/restoration, navigation announcements and axe checks |
| Recovery | Clean-host SQLite restore/rebuild set, restore quarantine and effect reconciliation, PostgreSQL PITR, named snapshot migrations, failed-upgrade rollback/forward-repair and agent profile/config recovery |
| Failure injection | API kill, scheduler double-start, partitioned worker continuing after lease loss, network timeout, lost receipt, before/after-commit disconnect, disk pressure and expired credentials |
| Scale | Named catalog/history fixtures, declared hardware, concurrent-operator/queue thresholds, pagination latency budgets and retention behavior |

### CI stages

1. **Pull request:** lint, type checks, locked dependency install, unit tests, ASGI contract tests, frontend unit tests, secret/dependency scan.
2. **Main/nightly:** PostgreSQL integration, migration snapshots, Playwright/axe, concurrency/failure tests.
3. **Windows integration:** Edge/browser and controlled worker smoke tests; Outlook tests should use draft or a controlled mailbox/recipient.
4. **Release candidate:** every named production-like snapshot upgrade, clean-host backup/restore/reconciliation, schema-compatible code rollback or declared forward-repair, and critical operator journeys.

Before implementation, the test owner defines versioned fixtures with row counts, file sizes, hardware profile, concurrency/latency budgets, deterministic or semantic artifact comparators, and a state-transition traceability matrix. Coverage percentage alone is not a release gate; each production state transition and security boundary must map to a named test.

## 19. Rollout plan

1. Publish maintenance/risk notice and freeze new side-effect categories.
2. Complete Milestone 0 containment and verify the recovery snapshot.
3. Complete the versioned migration prerequisite and ship Milestone 1 fixes behind explicit feature flags where compatibility is needed; test every flag combination for a secure default.
4. Enforce authentication first. Run only RBAC decisions in shadow mode under Milestone 0 containment; canary one operator, then enforce.
5. Run Outlook changes in draft mode, then one controlled recipient and one schedule.
6. Run durable-operation code on the current single node before separating agents.
7. Shadow/canary authenticated agents by capability cohort and use byte or semantic comparison as defined for that artifact/effect.
8. Observe at least one full relevant scheduling cycle before broadening the canary.
9. Perform the applicable schema-compatible rollback or declared forward-repair/disaster-recovery rehearsal before each milestone is declared complete.
10. Re-enable controlled network access only through Milestone 1C. Broader restart-sensitive scheduling waits for Milestone 2, and new production side-effect categories wait for the full relevant gates.

Every rollout must define:

- owner and communication channel;
- exact canary scope;
- dashboards and alert thresholds;
- expected old/new state mappings;
- stop conditions;
- rollback point and whether rollback is code-only, configuration-only, or restore-based;
- treatment of in-flight and unknown work.

## 20. Data migration rules

- Add immutable IDs and new state columns alongside old names/statuses first.
- Backfill deterministically through an audited admin review queue; pause dependent schedules/Flows until ambiguous people/report matches are resolved.
- Do not reinterpret historical `sent` as receipt-confirmed; mark it `legacy_unverified`.
- Convert validated current Flows/configurations into published revision 1 with the same behavior and snapshot hash. Missing, quarantined or unresolved dependencies become blocked drafts/inactive.
- Preserve occurrence and idempotency keys across every migration and cutover.
- For each operation/outbox/control-database cutover, pause scheduling/new claims, drain or classify in-flight Flows/pipelines/scans/SQL handoffs, map pending Outlook tasks/receipts, reconcile orphans and preserve next-run values.
- Validate counts, foreign keys, hashes, pending jobs, leases, outbox entries and next occurrence times before reopening.
- Preserve the previous database read-only until the rollback window expires.
- Use forward repair after new-version writes cross a non-reversible boundary unless reverse migration has been tested.
- After any snapshot restore, quarantine schedule materialization and claims until the backup high-water mark and every later known external effect have been reconciled.
- For timezone migration, record the former host zone, preview changed next occurrences, pause ambiguity, require operator confirmation and retain prior values for rollback analysis.

## 21. Ownership model

Names are intentionally omitted for senior review; assign one accountable owner per role.

| Role | Accountable for |
|---|---|
| Product/service owner | Risk acceptance, supported workflows, SLO/RPO/RTO and rollout approval |
| Platform/backend owner | Operation model, scheduler, jobs, leases, outbox, migrations and control DB |
| Security/identity owner | OIDC, RBAC, certificates, threat model, secrets and break-glass process |
| Windows automation owner | Agent runtime, Outlook/browser profiles, service accounts and capability inventory |
| Data owner | SQL idempotency, target permissions and ambiguous commit reconciliation |
| Frontend/product-quality owner | API client, lifecycle, operation UX, accessibility and browser coverage |
| Operations/release owner | Backups, restores, telemetry, alerts, runbooks, dependencies and atomic rollout |

No phase is complete if its operational owner has not accepted the runbook and recovery procedure.

## 22. Risk register

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Authentication rollout locks out legitimate operators | Medium / High | Shadow mapping, canary, break-glass test, explicit role owner |
| Existing Flows depend on arbitrary transforms or broad inherited secrets | High / High | Inventory first, signed registry, per-Flow canary, least-privilege compatibility period |
| Outlook retry duplicates messages | Medium / High | Outbox key, validated receipt, unknown state, no automatic replay |
| Flow SQL retry duplicates append data | High / High until fixed | Same-transaction ledger, idempotency key, explicit commit state |
| SQLite schema work is attempted before migrations are versioned | Medium / High | Complete the Milestone 1 migration prerequisite before any M1 schema change |
| PostgreSQL migration merely copies current process-local coupling | Medium / High | Durable domain model first; database migration later |
| Interactive browser/COM constraints prevent full service isolation | High / Medium | Dedicated automation account/VM, outbound agents, document residual risk |
| Timezone migration changes schedule behavior | Medium / High | Preview next occurrences, explicit DST/month-end policy, canary one full cycle |
| Frontend modularization changes behavior | Medium / Medium | Behavior-first regression tests and feature-slice extraction |
| Release rollback fails after schema mutation | Medium / High | Expand/contract, preflight, backup, rehearsal and declared point of no return |
| Security work delays urgent correctness fixes | Medium / High | Run Milestones 1A and 1B in parallel after containment |
| Operational history grows without bound | High / Medium | Retention policies, size metrics, archive and maintenance jobs |
| Restoring old control state replays forgotten external effects | Medium / Critical | Restore quarantine, high-water mark, external-effect reconciliation, accepted RPO |

## 23. Explicit non-goals

- No full rewrite.
- No big-bang React or visual redesign.
- No Kubernetes, message broker, multi-region deployment or premature service fleet.
- No immediate replacement of working GSCM/ASAP/Power BI automation logic.
- No claim of exactly-once Outlook delivery or recipient delivery confirmation.
- No automatic replay of unknown SQL, file or email effects.
- No simultaneous SQLite-to-PostgreSQL migration while durable semantics are still being invented.
- No production Vercel execution path for stateful Windows automation.
- No unrelated feature expansion before trust, truthful status, recovery and release gates are satisfied.

## 24. Senior review checklist

The revising senior engineer should explicitly answer:

- [ ] Are the Milestone 0 containment actions operationally possible without blocking required work?
- [ ] Is Entra/OIDC the chosen identity boundary, and who owns registration/certificates?
- [ ] Is the proposed role matrix sufficient, and which actions require approval rather than only a role?
- [ ] Are custom executable transforms still a supported product capability?
- [ ] Is Microsoft Graph available and desirable, or is Outlook COM a deliberate long-term constraint?
- [ ] What RPO, RTO, retention and availability targets are actually required?
- [ ] When, if ever, is multi-instance availability required?
- [ ] Is the dedicated PostgreSQL control database approved and separately operated?
- [ ] Which existing records need identity/status backfill and manual review?
- [ ] Which Flow groups are safe canaries for agent separation?
- [ ] What is the accepted behavior for missed schedules, DST changes and month-end dates?
- [ ] Are release ownership and break-glass procedures staffed?
- [ ] Are any findings accepted as residual risk? If so, by whom and until what date?

## 25. Program definition of done

The hardening program is complete only when:

1. Milestones 0 through the chosen deployment target have documented evidence and owner sign-off.
2. Threat model and route authorization matrix are reviewed.
3. Critical state machines and failure boundaries are covered by automated tests.
4. Backup restoration and the applicable schema-compatible release rollback or forward-repair/disaster-recovery path have been performed, not merely documented.
5. All production-changing actions have authenticated actors, operation IDs, immutable inputs, idempotency and truthful outcomes.
6. Dashboards, alerts and runbooks allow an operator to handle stalled, failed and unknown work.
7. Current architecture, README, installation material and historical `plan.md` no longer contradict deployed behavior.
8. The next production workflow passes the same security, durability, test and operational gates rather than adding a one-off execution path.
