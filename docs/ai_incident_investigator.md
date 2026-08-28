# AI Incident Investigator: value and evidence contract

## Product boundary

Metronome's detectors remain authoritative for whether a condition exists. The
investigator does not create, close, suppress, acknowledge, or modify Alerts.
Its job is narrower: use read-only operational evidence to distinguish the
most likely explanation, describe the real blast radius, and identify the one
next action that most reduces uncertainty or restores service.

The investigator must be allowed to abstain. A deterministic Alert with no AI
paragraph is better than confident generic advice.

## Diagnostic classes

| Class | Evidence that can support it | Valuable recommendation |
|---|---|---|
| Operational failure | Failed run/stage, error, missing artifact, rollback, worker loss | Exact eligible recovery or discriminating check |
| Monitoring-rule mismatch | Measured cadence, explicit schedule, prior operator resolution | Confirm cadence/owner, then review the specific rule |
| Expected timing | Business/fixed schedule and current observation relative to it | Wait until the named expected boundary, then recheck |
| Data-quality issue | Row-count/value trend and failing check | Inspect the named field/check and upstream producer |
| Dependency issue | Exact lineage and first stale/failed upstream | Repair or refresh the first abnormal dependency |
| Configuration issue | Current configuration conflicts with observed target/schedule | Review the named setting and proposed correction |
| External-service issue | Explicit remote/API/Power BI/Outlook failure evidence | Check the named service/session and preserve local work |
| No current issue | Current evidence directly contradicts the detector snapshot | Re-probe/reconcile; never auto-close from AI output |
| Insufficient evidence | No supported causal distinction is possible | Ask for the single missing fact that separates hypotheses |

## Evidence by incident family

### Source freshness

- Current rule, actual age, explicit refresh schedule and fixed weekdays.
- Durable distinct data-change dates and measured intervals, not repeated probe
  timestamps.
- Linked Flow schedule, last success/failure and error.
- Row-count movement, upstream/downstream lineage and impacted reports.
- Previous Alerts, operator notes and accepted/resolved outcomes.
- Filename semantics only as a weak hint; never as proof of business cadence.

### Flow execution

- First abnormal stage and complete bounded error.
- Comparison with the latest successful run.
- Expected versus observed artifact metadata and row counts.
- Worker heartbeat, resource ownership and transaction/rollback result.
- Server-computed recovery eligibility; the model cannot invent a replay path.

### Pipeline and dependencies

- Planned stage order and exact linked Flow/SQL/view/report identities.
- First failed or skipped stage, then the downstream stages that did not run.
- Current lineage, source activity and materialized-view/report refresh state.
- Concurrent work and locks that explain waiting without treating it as failure.

### Power BI and notification

- Dataset/report identity, last successful refresh and current API error.
- Whether upstream data is actually newer than the report.
- Outlook `submitted` is a handoff only; delivery requires separate evidence.

### Data quality and definition changes

- Current and prior values, sample coverage, thresholds and trend.
- Exact changed artifact and dependency/usage impact.
- Query text and sensitive data stay outside model context unless a future
  explicitly reviewed projection is introduced.

## Output contract

The visible result is one paragraph of no more than 100 words:

**What happened:** the first supported abnormal condition or rule mismatch.
**Impact:** concrete affected data/report/stage, without hypothetical inflation.
**Suggested action:** one specific action or next discriminating check.

Every claim and recommendation cites server-issued evidence references. The
paragraph must separate detector truth from causal inference. For example, a
source may be confirmed outside a 30-day rule while the supported diagnosis is
that its historical annual cadence does not fit that rule.

## Automatic rejection

Reject the model result and retain the deterministic Alert when it:

- cites evidence that was not returned by a read tool;
- recommends a recovery operation that server preflight says is ineligible;
- diagnoses cadence/rule mismatch without history, an explicit schedule, or
  prior operator evidence;
- treats names such as `mapping` or `master` as proof;
- exceeds 100 words or returns more than one action;
- recommends automatic suppression or configuration mutation;
- gives generic advice without identifying the exact log, connection, check,
  rule, owner question, or operation that would resolve uncertainty.

## Evaluation

Review live incidents against six questions: correct first abnormal stage,
supported claims, useful information beyond the detector, executable first
action, actual cause/fix, and estimated investigation time saved. Retain the
feature only if it avoids invented facts, improves the first action, and saves
operator time. Resolved incidents should later become retrieval evidence, not
unreviewed training data.
