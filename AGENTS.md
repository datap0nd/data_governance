# Working in this repository

Metronome is an internal FastAPI app that monitors Power BI reports and data
sources and runs governed browser/SQL automations ("Flows"). The owner delegates
implementation to coding agents and tests from GitHub `main`, so this file is
the canonical contract for Codex and Claude Code alike. `CLAUDE.md` imports it;
nothing policy-relevant lives only there.

## Done means

Merged to `main` at the tested head, release test plan and report linked in the
PR, and a reply that names the delivery state. Work left on a branch does not
exist for the owner, who updates the running app from `main`. Keep going until
that is met; the only planned pause is owner feedback on a materially changed UI
journey ([DESIGN.md](DESIGN.md)). Questions, analysis and advice are answered
directly — no branch, PR or test package.

## Precedence and authority

The owner's instruction in the current task wins over this file, except the
invariants. Standing authorization covers branching, pushing, opening the PR and
the head-pinned merge once required checks pass. Ask first about branch
protection, workflow secrets, live systems, production data or deleting owner
data. Scratch files stay out of commits.

## Invariants

Each protects something a PR cannot undo:

- PostgreSQL probe credentials are read-only: no writes, no DDL. Flow SQL writes
  use the separate `DG_UPLOAD_PG*` credentials.
- Schedules, calendar maths and displayed instants use Dubai time; monitoring
  timestamps stay UTC.
- Scheduled execution fails closed when a saved page, visual, column or rule
  disappears; it never guesses a replacement.
- Never weaken, skip or delete a test to make a run pass — a failing test is a
  finding.
- Never commit credentials, cookies, private portal URLs, report data or
  unsanitized traces; reference protected evidence by an opaque identifier.
- Work-PC, live portal, authentication and hardware checks are opt-in: run, plan
  or report them only when the owner asks in the current task.
- Never invoke a Metronome/live-fix skill unless the owner names it in the task.

## Delivery workflow

1. Branch from current `origin/main`; preserve unrelated work.
2. Verify locally (below) and write the release plan and report while
   implementing.
3. Commit, push, open the PR to `main`.
4. Wait for the required final-head `Merge ready` check, record its run URL and
   the tested head SHA in the PR's testing section, then merge pinned to that
   head.
5. Open the reply with the delivery state: *implementation ready*, *local checks
   complete*, *CI complete* or *merged*. A merge is not a deployment, and an
   unmerged change is never described as available. If the merge did not happen,
   say so first.

## Verification

`tools/check.py` (Linux, macOS, Windows) and `tools/check.ps1` (PowerShell)
behave identically: explicit selectors, database, temp, browser-profile and Flow
paths isolated under an ignored `.test-runs/<id>/`, and a machine-readable
`result.json` to cite as evidence.

```bash
python tools/check.py setup       # once: checkout-owned Python 3.13 .venv
python tools/check.py preflight   # one actionable environment diagnosis
python tools/check.py verify --test tests/test_flows.py::test_name --syntax app/flow_worker.py
```

```powershell
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flows.py::test_name -SyntaxPath app/flow_worker.py
```

The fixtures are disposable and reach nothing in production, so run the affected
tests, fix what the change broke and rerun them without asking at each step.

Run one smallest non-overlapping affected set plus applicable syntax checks:
required final-head CI is the authoritative full regression for application,
dependency and test changes, so a local superset only spends wall time and
produces duplicate evidence. Documentation, repository-policy, PR-template and
workflow-only changes use the lightweight CI scope gate. `--full`/`-Full` is
diagnostic-only and needs a recorded reason; `--reuse`/`-Reuse` holds only while
the fingerprint matches. Use the checkout-owned `.venv`, never a sibling,
temporary or bundled agent environment, so results match CI's locked
dependencies. After a failure rerun the failed case and the companions it needs;
after a rebase rerun application tests only if application/test code changed or a
related conflict was resolved.

## Testing documentation

Every PR merged to `main`, documentation and maintenance included, carries a
release test plan and report under `docs/testing/releases/`, linked from the PR
and the reply. [The testing workflow](docs/testing/README.md) owns the procedure
and templates. Scale the package to the change — a documentation-only change
gets documentation checks, not invented app tests — and record actual commands,
revision, environment, results, skips and warnings. Never prewrite a result for
an unfinished check; identify synthetic fixture/browser tests as synthetic.
**NOT RUN** and **BLOCKED** are correct for in-scope automated checks still
pending, usually final-head CI; checks that were never in scope are omitted
entirely rather than listed as placeholders.

## Codebase map

| Path | What lives there |
| --- | --- |
| `app/` | FastAPI service: `main.py`, `models.py`, `database.py`, `config.py` |
| `app/routers/` | HTTP endpoints, one module per feature area |
| `app/static/` | Vanilla JS and CSS UI; no build step, no framework |
| `app/flow_*.py` | Flow engine: recording, replay, browser/worker runtime, SQL, paths, delivery |
| `app/ai/` | LLM provider abstraction and the read-only operations agent |
| `app/scanner/`, `app/checks/` | Power BI scanning, freshness probing, data-quality rules |
| `tests/` | `pytest` suites plus `tests/test_*.mjs` Node UI contract tests |
| `tools/` | Verifiers, `ci/merge_gate.py`, installer and worker scripts |
| `docs/` | Feature docs, `docs/testing/` release packages, `docs/archive/` history |

Domain terms: **ASAP**, **GSCM**, **NASCA** and **Luna** are vendor portals a
Flow drives through a recorded browser session; a **Flow** is a saved,
schedulable automation (fetch, optionally transform, then write to SQL, a folder
or email); a **Recording** is the captured browser steps it replays; a
**Pipeline** is the lineage view over scanned reports; a **Worker** is the
headed or headless process that executes Flows.

## Read when relevant

No pre-read sweep. Open a document when its subject is in play:
[DESIGN.md](DESIGN.md) before UI work — it owns the design contract and the
usability review; [the testing workflow](docs/testing/README.md) when writing the
release package; `docs/flow_*.md` and `docs/recorded_flows.md` when touching
Flows; [README.md](README.md) for operator setup; [PRODUCT.md](PRODUCT.md) for
product context. `GEMINI.md` and [the field-agent workflow](docs/gemini_field_agent.md)
cover the separate command-line assistant that observes live Flows on the work
PC; they bind that assistant, not work in this repository.

## Working style

Act on the request as scoped and make routine judgment calls; ask only when two
readings would produce materially different work. If a file in this repository
made you stop or change approach, name it and quote the line so the owner can
fix the instruction. Open the reply with the delivery state, link the plan and
report, and write plain prose.
