# Metronome AI Agent Plan — Qwen3.8-27B

**Status:** Alert-bound incident slice implemented; later phases remain a draft for senior engineering review

**Date:** 2026-08-27

**Model assumption:** The requested “Qwen 27 3.8b” means the official `Qwen/Qwen3.8-27B`: Qwen generation 3.8 with a 27-billion-parameter language model.

## 1. Decision

Build **one tool-using Operations Investigator** first. Give it several focused modes backed by the same runtime and tool registry. Do not begin with a swarm of agents or an autonomous browser operator.

### Implemented first slice (2026-08-27)

The incident-focused slice is now implemented for one exact `flow_run` or `pipeline_run` at a time:

- durable `agent_runs`, `agent_steps`, and `agent_evidence` records with restart recovery and cooperative cancellation;
- asynchronous single-worker execution with wall-clock, model-turn, per-turn, per-tool, aggregate-context, response-size, and transcript-size bounds;
- strict native OpenAI-compatible tool-call parsing for Qwen, with hidden reasoning kept only in the in-memory model transcript;
- structured terminal output whose conclusion, facts, inferences, and recommendations must cite evidence observed during that investigation;
- exact-scope Flow summary, events, artifacts, and comparable-run tools;
- exact-scope Pipeline summary plus derived access only to Flow runs explicitly linked by that Pipeline's durable steps, including their events and artifact metadata;
- shared Resume, Retry SQL, and Run Fresh preflight logic, re-read before a recommendation is accepted and revalidated again by the real operational endpoint;
- canonical operational Alerts with immutable exact-run occurrences for Flow and Pipeline failures, plus recorded Power BI reconnect occurrences;
- transactional binding of each Alert-originated analysis to its action, occurrence, evidence revision, and server-derived run focus;
- automatic supersession when Alert evidence changes or its lifecycle closes: historical traces remain visible while stale recommendations are removed;
- an inline Analysis surface in each Alert, with contextual run-history shortcuts, durable polling, focus-race protection, escaped structured results, allowlisted internal evidence links, and no model-generated action buttons;
- deterministic scripted-provider and UI boundary tests; no live model is required in CI.

Not implemented in this slice: daily briefings, arbitrary platform Q&A, change-impact analysis, documentation drafting, communications drafting, multimodal analysis, browser control, or any approved/executable agent action. Those remain evaluation-gated later phases.

The first release should answer two questions extremely well:

1. **Why did this Flow or Pipeline fail, what actually completed, and what should I do next?**
2. **What needs my attention today, ranked by operational and report impact?**

This is the highest-value use of the information Metronome already records. It also creates the foundation for change-impact analysis, documentation, communications drafting, and approved operational actions later.

## 2. Legacy AI surface and remaining gaps

The new operational path is the bounded agent described above. Metronome also retains older AI-themed endpoints that are not part of that agent contract:

- [`app/ai/router.py`](../app/ai/router.py) builds one small dashboard summary and makes one synchronous model call.
- [`app/ai/llm_provider.py`](../app/ai/llm_provider.py) supports plain text only: no tools, structured responses, streaming, conversation state, cancellation, or usage capture.
- The request model accepts `context`, but that field is unused.
- Legacy chat cannot inspect Flow runs, Pipeline steps, query diffs, worker state, schedules, Outlook outcomes, documentation, or exact lineage on demand.
- Briefing and report-risk endpoints always use deterministic mock providers even when a real model is configured.
- The frontend contains a caller for `/api/ai/suggestions`, but no such backend route exists. Report-risk and suggestion renderers also appear to have no live call sites.
- [`app/ai/prompts.py`](../app/ai/prompts.py) is not used by the real model path.
- Documentation AI asks the model to define formulas from measure names while deliberately withholding the DAX definitions. The single-report path can therefore produce plausible inventions, and the batch path writes those suggestions into documentation. This should become an evidence-backed, review-before-save draft flow.
- The operational agent has deterministic protocol, grounding, lifecycle, and UI tests; it still needs a live-Qwen evaluation set before model-assisted conclusions should be treated as production-proven.
- Without `DG_AI_API_URL`, the operational UI deliberately uses a deterministic recorded-state preview rather than pretending Qwen is connected.

The positive part is that the real chat already sends only an allowlisted summary rather than dumping raw `connection_info`. That instinct should remain, but typed tools should replace the static context dump.

## 3. Qwen3.8-27B assessment

The [official model card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/README.md) describes a dense native vision-language model with a 27B language model, 64 text layers, image/video support, 262,144 native context, optional extension to one million tokens, and adjustable reasoning effort. It can be served through OpenAI-compatible vLLM or SGLang endpoints.

The model is a credible fit for Metronome. The most relevant vendor-reported scores are:

| Capability | Qwen-reported result | Meaning for Metronome |
|---|---:|---|
| CoWorkBench long-horizon office work | 70.7 | Strong signal for multi-step operational investigation |
| OmniDocBench 1.5 | 91.1 | Strong report and document understanding |
| CharXiv chart reasoning | 83.7; 90.2 with code interpreter | Promising for chart and BI evidence review |
| OSWorld-Verified computer use | 84.3 | Visual troubleshooting may be useful later |
| WebArena-Verified browser use | 64.8 | Still too fallible for unchecked browser actions |
| ClawEval-MM multimodal tool use | 57.4 pass@3 | Tool use requires validation and bounded loops |
| JobBench professional tasks | 33.4 | It will fail ordinary-looking work more often than demos imply |
| Agents’ Last Exam | 20.4 pass@1 | It is not reliable enough for unsupervised operational autonomy |

Several results are vendor-run, in-house, harness-dependent, or model-judged. They justify a pilot, not trust. Metronome’s own evaluation set is the release gate.

The raw checkpoint does **not** include web search, citations, source verification, durable memory, or safe action execution. Those are responsibilities of the Metronome agent harness.

### Deployment reality

The current development laptop has about 15.6 GB RAM and no NVIDIA GPU exposed through `nvidia-smi`; it is not a realistic host for this 27B model. Use a remote inference server or the existing configurable AI endpoint.

As a rough lower-bound calculation, 27B weights require about 54 GB at two bytes per parameter, 27 GB at one byte, or 13.5 GB at four bits before runtime overhead, KV cache, vision components, and activations. The full 262K context is not a desktop default. Start with a 16K–32K agent budget and let tools retrieve only what the current question needs.

For native vLLM tool parsing, the current [vLLM Qwen3.8 recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) uses the Qwen reasoning parser and `qwen3_coder` tool-call parser. If the serving endpoint cannot expose native OpenAI-compatible tool calls, the official [Qwen-Agent framework](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/features/) can supply the canonical function-call loop. Do not use a stopword-based ReAct parser for a reasoning model; [Qwen’s function-calling guidance](https://github.com/QwenLM/Qwen3/blob/main/docs/source/framework/function_call.md) warns that reasoning text can collide with stopwords.

## 4. Agent modes, in priority order

All modes use one orchestrator. A mode changes the prompt, available tools, output template, and budget; it is not a separate autonomous service.

### P0 — Incident Investigator

User question: “Why did this run fail, what completed, and what is the safest next step?”

It inspects:

- Flow run state, progress events, phase timings, worker, traceback, files, checksums, and SQL outcome;
- the latest comparable successful run;
- resumable and SQL-ready artifacts;
- Pipeline plan, run steps, locks, materialized-view state, Power BI state, and notification state;
- relevant scanner and credential readiness.

It must distinguish:

- **Resume** when completed downloadable work is reusable;
- **Retry SQL** when valid SQL-ready files already exist;
- **Run fresh** when nothing reusable completed;
- **Do not retry** while a run or physical-target lock is active;
- Outlook attachment Flows, which cannot Resume;
- an unknown materialized-view or external-effect outcome, which requires inspection rather than replay.

### P0 — Daily Operations Briefing

User question: “What actually needs my attention today?”

Use deterministic backend facts and scoring: open actions, freshness, report usage/impact, failed or slow runs, overdue refreshes, scan health, worker readiness, scheduled email state, and unresolved ownership. The model explains and groups that ranking; it does not invent the health score.

### P1 — Change-Impact Analyst

User question: “This M query or materialized-view definition changed. What could it affect?”

Use exact query diffs, version history, canonical PostgreSQL identities, lineage closure, affected reports, usage, owners, and current documentation. It must distinguish semantic change, whitespace-only change, removal, and revert. Similar names are never evidence of lineage.

### P1 — Pipeline Preflight Explainer

Translate the existing authoritative Pipeline plan into plain language: ambiguous/stale identity, duplicate writers, active locks, dependency cycles, unavailable workers, unresolved Power BI targets, missing recipients, or stale credentials. The agent explains blockers; backend preflight remains the decision maker.

### P1 — Documentation Curator

Draft report purpose, business definitions, formulas, lineage summary, known issues, and a recent-change summary. Save as draft and show evidence. Do not overwrite populated fields or publish automatically.

### P1 — Communications Drafter

Create a concise Outlook-ready owner update from confirmed incident facts. Initially this returns text only. Later it may create a visible draft after confirmation. “Submitted to Outlook” must never be described as delivered, and an unknown submission must never be resent automatically.

### P2 — Configuration Copilot

Suggest Flow settings, freshness rules, schedule ordering, owner fixes, or a Pipeline repair. Produce a validated diff or pre-filled form for review. The model does not directly edit configuration.

### P2 — Multimodal Investigator

Use Qwen’s vision input on stored diagnostic screenshots, Power BI error dialogs, browser failure captures, and charts alongside structured run evidence. It should recommend a next diagnostic step, not drive the browser initially.

## 5. Initial tool registry

Tools return small, typed JSON with stable entity IDs, observation timestamps, and deep links. They call application services or read models—not Metronome’s HTTP API over localhost.

| Tool | Purpose | First release |
|---|---|---|
| `get_platform_attention_queue` | Deterministic priority queue and supporting facts | P0 briefing |
| `get_flow_run` | Run state, phases, worker, outcome, timings | Yes |
| `get_flow_run_events` | Ordered progress events and traceback | Yes |
| `get_flow_run_artifacts` | Files, validation state, checksums, resume/SQL readiness | Yes |
| `compare_flow_runs` | Current failure versus a prior successful comparable run | Yes |
| `get_pipeline_run` | Plan, steps, blockers, locks, outcome | Yes |
| `get_worker_readiness` | Required worker mode and current availability | Later |
| `get_source_health` | Latest observation and freshness rule | P0 briefing |
| `get_lineage_impact` | Exact upstream/downstream closure and reports | P1 |
| `get_query_change` | Exact normalized diff and version metadata | P1 |
| `get_outlook_dispatch` | Draft/submitted/failed/unknown evidence | P1 |
| `search_documentation` | SQLite full-text/field search over current docs | P1 |
| `draft_owner_update` | Structured draft only; no Outlook side effect | P1 |

Avoid a generic `run_sql`, `read_database`, filesystem, shell, browser, or arbitrary-URL tool. The model needs operational questions answered, not unrestricted access.

## 6. Evidence and action contract

Every response is structured into:

1. **Conclusion** — concise answer.
2. **Observed facts** — status, timestamp, entity/run ID, and field returned by a tool.
3. **Inference** — the model’s explanation, clearly labeled.
4. **Recommended next action** — including why it is appropriate.
5. **Unknowns** — missing, conflicting, or stale evidence.

Every material factual claim links to a Metronome entity, run, or captured observation. Tool outputs include `observed_at`; the UI shows it. The model must not calculate authoritative source health, identity, Pipeline eligibility, or Outlook delivery state itself.

The first release has read tools only. The only automatic writes are the agent’s own run/step/evidence records and saved draft text.

Later, a visible preview and confirmation are required before:

- running, resuming, stopping, or retrying a Flow;
- starting a Pipeline or refreshing a materialized view;
- editing schedules, owners, freshness rules, or configuration;
- resolving an alert;
- publishing documentation;
- creating or sending Outlook mail.

This boundary prevents duplicate or misleading operations; it is not security theater.

## 7. Runtime architecture

```text
Expanded Alert / contextual run shortcut
        │ create agent run
        ▼
Agent run service ──► SQLite action occurrence, run, step and evidence records
        │
        ▼
Qwen3.8 orchestrator ──► typed read-tool registry
        │                         │
        │                         ├── Flow/Pipeline read models
        │                         ├── lineage/query history
        │                         ├── scanner/worker state
        │                         └── Outlook/documentation state
        ▼
Structured conclusion + evidence + text recommendation
        │
        └── no operational command in the implemented slice
```

Implement a small explicit tool loop rather than adopting a general agent platform immediately:

1. Send system prompt, conversation, and mode-specific tool schemas.
2. Validate every model-generated tool name and arguments against Pydantic models.
3. Execute the tool with a per-call timeout and record its result.
4. Return the result as a tool message.
5. Stop on final structured output, maximum calls, wall-clock deadline, cancellation, or repeated identical call.

Recommended starting limits: 12 tool calls, 3 repeated-call strikes, 3 minutes wall time, 16K–32K input budget, and 4K–8K final-output budget. Tune from traces rather than exploiting the maximum context window.

Use thinking mode with `medium` reasoning for incident and impact work, then benchmark `xhigh` on difficult cases. Use low/no-think for short formatting and drafting tasks. Parse and store the final answer and tool evidence; do not expose or treat preserved hidden reasoning as an audit record.

## 8. Durable records

The implemented append-oriented model contains:

- `action_occurrences`: immutable Alert evidence revision, exact focus, summary, bounded evidence, and observation time;
- `agent_runs`: mode, user request, exact focus, optional Alert/revision binding, status, actor, model, reasoning setting, start/end, error, usage, and supersession state;
- `agent_steps`: tool name, validated arguments, status, timing, compact result/error;
- `agent_evidence`: entity type/ID, observation timestamp, label, deep link, supporting step;

Ordered conversation messages and `agent_action_proposals` remain later-phase additions.

Store compact structured facts, not full database dumps or hidden reasoning. A retention policy for agent traces remains follow-up work.

## 9. API and UI

Implemented endpoints:

- `GET /api/actions/{id}/occurrences` — immutable Alert occurrences and the latest analysis for each revision;
- `POST /api/ai/operations/runs` — question plus either an exact run focus or an Alert/occurrence binding;
- `GET /api/ai/operations/runs/{id}` — durable state, structured result, evidence, binding freshness, and supersession metadata;
- `POST /api/ai/operations/runs/{id}/cancel`.

Runs execute asynchronously and the UI polls durable state. Server-Sent Events may be considered later if polling becomes a measured usability problem.

The Alert-owned Analysis surface shows:

- the current investigation and elapsed time;
- tool activity in human language (“Reading Flow run #184”);
- facts versus inference;
- clickable evidence;
- a text-only recommendation with no execution control;
- historical analysis with stale recommendations suppressed.

Contextual run-history/deep-link analysis remains available in a drawer, but there is no generic floating AI launcher. All API failures are rendered as failures, never as model answers.

## 10. Evaluation before rollout

Create fixture-shaped cases with expected tools, required facts, forbidden claims, and forbidden actions:

1. stale source affecting several high-usage reports;
2. report behind a newer upstream source;
3. partial Flow where Resume is correct;
4. SQL-stage failure where Retry SQL is correct;
5. Outlook attachment Flow where Resume is forbidden;
6. duplicate SQL writer or active target lock;
7. ambiguous or stale exact identity versus harmless name similarity;
8. materialized-view dependency cycle;
9. restart during MV refresh producing `unknown/requires_inspection`;
10. semantic query change, whitespace-only change, and revert;
11. email deferred because Power BI state is stale;
12. Outlook `submitted`, `failed`, and `unknown` outcomes;
13. partial scanner failure versus total failure;
14. missing or ambiguous owner/recipient;
15. insufficient evidence requiring abstention;
16. prompt-like instructions embedded in a report name, query, or traceback;
17. user says “fix it” without approving an operational command.

Initial acceptance gates:

- 100% of material factual claims have a valid evidence record;
- zero forbidden tool calls or unapproved operational effects;
- no `submitted`/`delivered` conflation and no auto-retry recommendation for an unknown effect;
- at least 90% correct action class on the curated incident set;
- required abstention on every missing-evidence case;
- the same tool arguments validate under replay;
- acceptable latency on the actual inference endpoint.

Run the suite against Qwen `medium` and `xhigh`, plus the current simple LLM baseline. Keep fixture outputs and scores by model/server version. Do not fine-tune until prompt/tool/evidence failures have been separated from model failures.

## 11. Delivery plan

### Phase 0 — Model and contract spike (2–4 days)

- Connect `Qwen/Qwen3.8-27B` through the OpenAI-compatible endpoint.
- Verify reasoning parsing, native tool calls, structured arguments, cancellation, and streaming against 5–10 synthetic tools.
- Record latency and memory/server limits at 8K, 16K, and 32K contexts.
- Build the first 10 evaluation cases before product code.

**Gate:** Qwen selects the correct read tool and produces valid arguments consistently enough to proceed.

### Phase 1 — Read-only Incident Investigator (1–2 weeks)

**Implementation status:** harness, Alert binding, deterministic preview, UI, and automated boundary tests are complete. Live-Qwen evaluation and inference-host selection remain release gates.

- Add durable agent runs/steps/evidence.
- Implement the five Flow/Pipeline investigation tools.
- Add the bounded tool loop and structured final response.
- Add Alert-owned Analysis plus contextual Flow and Pipeline run shortcuts.
- Ship only after incident evals and cancellation/reload behavior pass.

### Phase 2 — Daily Briefing and Impact Analyst (1–2 weeks)

- Add attention queue, source health, lineage impact, query diff, and documentation tools.
- Replace the mock briefing with a real evidence-backed briefing.
- Add report/source focus and clickable evidence.

### Phase 3 — Drafts and approved actions (1–2 weeks)

- Add action proposals and a preview/confirmation UI.
- Start with owner-update drafts and one reversible command such as queueing a fresh source probe.
- Reuse existing backend validation and status handling; the agent never bypasses it.

### Phase 4 — Multimodal evidence and selective automation

- Add stored screenshots/charts as evidence only where they improve an existing investigation.
- Expand approved commands one by one, each with fixtures for duplicate, conflict, unknown, and stale-plan behavior.

## 12. Explicitly avoid for now

- multiple collaborating agents;
- autonomous web or desktop control;
- arbitrary SQL, shell, filesystem, Python, URL, or Outlook-send tools;
- dumping the full SQLite database into a 262K prompt;
- a vector database before SQLite search and typed retrieval are insufficient;
- one-million-token serving as a product requirement;
- model fine-tuning before a measured prompt/tool/evaluation baseline;
- allowing the model to decide whether a Pipeline is valid, data is fresh, or an email was delivered.

## 13. Senior review decisions

The revising engineer should decide:

- Which inference server will host Qwen, and which exact checkpoint/precision?
- Does its OpenAI-compatible endpoint emit native `tool_calls`, `reasoning_content`, usage, and cancellation correctly?
- Which existing service functions become the first typed tools?
- What is the target incident set and who signs off the expected next action?
- Is polling sufficient for Phase 1, or is SSE required immediately?
- What retention is appropriate for tool results and tracebacks?
- Which single reversible action, if any, is safe for the first approval-flow pilot?

The product test is simple: the agent should reduce the time needed to understand and recover a failed data-warehouse operation without making Metronome less truthful about what happened.
