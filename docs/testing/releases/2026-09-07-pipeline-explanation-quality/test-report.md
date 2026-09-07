# Pipeline explanation quality gate: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: <filled in under Merge evidence>
- Evidence cutoff (UTC): 2026-09-07
- Tested code revision: branch `claude/pipelines-artifact-overlap-9flcel`
  working tree on top of the PR #84 commit; exact SHA in the PR testing section.
- Environment: Linux 6.18 container, Python 3.11.15, Node 22.22.2. No Qwen
  endpoint and no PostgreSQL are available here; model behavior is exercised
  through fake providers that return generic and specific answers.
- Overall finding: automated checks PASS; live checks NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| S-01 quality | `python -m pytest tests/test_pipeline_insights.py -q` | branch working tree, Python 3.11.15 | PASS, 28 passed (7 new: SQL/M fact extraction incl. pg_get_viewdef parentheses and casts, generic rejection reasons, specific acceptance, retry-with-feedback, persistent-generic fallback with breakdown, disabled-feature reasons, live definition fetch and cache) | console |
| S-01 lineage | `python -m pytest tests/test_lineage_depth.py -q` | same | PASS | console |
| S-01 frontend | `node --check app/static/app.js`; `node tests/test_pipeline_insights_display.mjs`; `node tests/test_lineage_display.mjs`; `node tests/test_lineage_edges.mjs`; every other Node test in `.github/workflows/tests.yml` | same, Node 22.22.2 | PASS | console |
| Full Python suite | `python -m pytest tests -q` | same | see Merge evidence | console |
| Parser smoke | ad-hoc run of `sql_facts`/`m_facts` on a joined, filtered, grouped SQL, a pg_get_viewdef-formatted definition with `::date`/`::text` casts and `ANY (ARRAY[...])`, a subquery in WHERE, and an M expression with SelectRows, RemoveColumns and AddColumn | same | facts extracted as expected; fallback text names joins, join columns, predicates and grouping | this report |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-01 to T-07 | NOT RUN | No Qwen endpoint, PostgreSQL catalog or real report lineage in this environment. | Owner updates the app from `main`, runs **Scanner > Pipeline explanations** once (all edges regenerate under the new prompt version) and walks the cases. |

## Findings, limitations and retests

- The digest parser is regex-based and best-effort. It handles pg_get_viewdef
  formatting, aliases, USING joins, casts, and subqueries in WHERE, but a
  definition it cannot parse yields an empty digest; the gate then only
  enforces minimum length, non-empty evidence columns, and the ban on "A feeds B"
  phrasing, and the fallback text says the definition contains no detected
  join or filter. The model still receives the full SQL.
- The gate can reject a correct answer that describes a join without using the
  word join, merge, matched, or lookup; the retry feedback names the exact
  expectation, so a capable model passes on the second attempt.
- Model-call ceiling is unchanged at ceil(E/4) + E per run: each edge gets at
  most one single-edge follow-up call.

## Merge evidence

Pending until the PR's final CI run finishes; the PR testing section records
the final run URL, tested head SHA and result before merging.
