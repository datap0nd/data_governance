# Excel worksheet selection: draft test report

- [Plan](test-plan.md); [browser evidence](browser-evidence.md); [exact local verification records](local-checks.json).
- Evidence cutoff: 2026-09-12 17:00 UTC. Later results belong in a dated addition and in the PR testing section.
- Baseline: `c3f0869adf03e4ead1d497af22a20a1a8115bedd`, with the uncommitted changes in this branch. Per-invocation source fingerprints distinguish the working versions tested; these results do not claim that the final PR head has passed CI.
- Environment: Windows 10.0.26200, PowerShell 7.6.5, checkout-owned Python 3.13.15, Playwright 1.62.0, synthetic Chromium downloads and synthetic COM worksheet objects.
- Delivery state: backend implemented; production UI pending owner preview feedback. Not merged.

## Executed checks

| Evidence | Outcome |
| --- | --- |
| `20260912T162625333Z-4872-c25aa436` | **80 passed**, zero failures/errors/skips, 41.66s pytest time. Initial worksheet rules, formats, API persistence, original retention, NASCA selection/cleanup, and local-file regression coverage; applicable syntax checks passed. |
| `20260912T163659862Z-14488-31745db7` | **19 passed, 10 failed**. The first synthetic recorded default-guard case passed. Remaining database-backed cases failed before execution because the Windows verifier shared the same `Portable sales (id 1)` folder between separate fixture databases. Retests below use separate invocations. |
| `20260912T164137974Z-33904-2dfc6a19`, `20260912T164137986Z-24840-c0f3b5b2` | **1 passed each**. Synthetic recorded downloads delivered exactly the appended named rows and the single named sheet to the captured SQL loader. |
| `20260912T164448693Z-28500-e3fb87ac`, `20260912T164448679Z-25448-9b1994dc` | **1 passed each**. Missing selected sheet and incompatible selected columns fail before any SQL call. |
| `20260912T164448694Z-10536-aee2730c`, `20260912T164448699Z-11412-5e46c771`, `20260912T164719158Z-36392-3ddd7759` | **1 passed each**. Existing recorded text-as-Excel normalization and both synthetic NASCA inputs still reach the intended normalized SQL input. |
| `20260912T164719158Z-35580-19d3628f`, `20260912T164719166Z-36696-5806b687` | **1 failed each**. Concurrent portable-script tests collided on host-wide Flow execution locks. These are test orchestration failures, not worksheet mismatches. |
| `20260912T164719158Z-27860-b1e28972` | **1 failed**. Portable CSV execution reached run-folder creation and hit the Windows path-length limitation under the verifier's long temporary root. No worksheet implementation conclusion is drawn from this failure. |
| `20260912T165441326Z-40396-195f0731` | **3 passed, 2 failed**. Structured error evidence, zero-data failure and worker capability gating passed. Two new generic-portal fixtures omitted their required output directory. |
| `20260912T165653140Z-39964-502237a4` | **2 passed** after correcting that fixture setup. Generic portal downloads enforce the default guard and preserve the chosen append order. Syntax check passed. |

Exact commands, timestamps, selections, fingerprints and JUnit totals are in `local-checks.json`. Counts above are per invocation; repeated checks are not presented as unique coverage. No skips occurred in the listed completed checks. The retained failures are not relabeled as passes.

## Pending checks

| Check | State | Next action |
| --- | --- | --- |
| Owner preview feedback and production Flow UI | Pending | Review the clickable preview, then implement and verify the actual settings and run-error recovery controls. |
| Portable-script regression on this Windows verifier | Environment-limited | A short temporary path and sequential execution are needed. Required CI runs these cases in its supported isolated environment; record its result. |
| Full Python regression / final-head Merge ready | Not run at cutoff | Record the CI URL and exact tested SHA in the PR before a head-pinned merge. |

## Findings and scope

The existing worker already contained a `different columns` error for multi-sheet Excel normalization. This change makes worksheet selection explicit and records actionable file/sheet errors before SQL. It does not establish how many historical production runs contained that error; no production history count was obtained in this implementation pass.

The tests preserve all selected rows, including small sheets, duplicate rows and `Total` headers. Append still requires the same normalized column names in the same order. No heuristics were added to omit summary sheets or small data sets. SQL loading code and browser context lifecycle are unchanged; worksheet configuration failures stop acquisition retries.

Backend review covered selection validation, exact names/order, raw-workbook fallback, source retention, capability compatibility, local-file receipt identity, and error propagation. The generic portal path was found to bypass the shared Excel reader and was connected to it; its two focused cases passed after correcting test setup.

## Merge evidence

Pending. This draft report stops before owner UI approval and final-head CI. No merge or deployment is claimed.
