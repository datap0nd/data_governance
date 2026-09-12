# Archive

These documents are kept for history. They describe earlier plans, earlier
product names and earlier machine state, and several of their statements are no
longer true of the code in this checkout — so none of them is current guidance
for a person or an agent working here. Current guidance is [AGENTS.md](../../AGENTS.md),
[README.md](../../README.md), [PRODUCT.md](../../PRODUCT.md), [DESIGN.md](../../DESIGN.md)
and [the testing workflow](../testing/README.md); the still-accurate operator
content from `instructions.md` now lives in `README.md`.

| File | What it was | Why it is not current |
| --- | --- | --- |
| `instructions.md` | "MX Analytics" Windows setup guide | Product is Metronome; `owners.csv` and `powerbi_links.csv` no longer have loaders. Its service, `endpoint_url.txt`, environment-variable and read-only PostgreSQL content was carried into `README.md`. |
| `plan.md` | Original TMDL/Docker/Pico CSS build plan | Neither Docker nor Pico CSS exists in the app; `docs/production_hardening_plan.md` supersedes its production assumptions. |
| `pending_steps.md` | Manual Power BI owner-table steps | References `update.ps1`, which no longer exists; scanning reads `.pbix` directly. |
| `extra.md` | Draft of an analysis email | Unrelated to the codebase and written with placeholders. |
| `additional_instructions.html` | Handout for report owners adding owner tables | Same superseded manual flow as `pending_steps.md`. |
| `agent_handoff.md` | Session handoff pinned to commit `2143650` | That revision is not resolvable in this checkout and its "next step" was overtaken by later releases; live portal detail like this is no longer committed. |
