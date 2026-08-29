# AGENT.md — LLM Gateway

This file is the entrypoint for any AI agent (Antigravity, or otherwise) working in this repository. Read this fully before writing or modifying any code.

## Project Summary

An LLM Gateway: a production-style API proxy in front of multiple LLM providers (Gemini, Claude, Groq, Ollama) that adds per-team rate limiting, budget enforcement, automatic fallback/circuit breaking, and full observability (Prometheus + Grafana). Built as a portfolio project — solo, full scope committed, phased build with no half-finished layers.

## Required Reading Before Any Task

Full specs live in `context/`. **Always consult the relevant doc before implementing — do not guess at schema, routes, or conventions that are already specified.**

| File | Read this when you need to know... |
|---|---|
| `context/01_PRD.md` | What's in scope vs. explicitly out of scope. Check this first if a request seems to expand the project. |
| `context/02_TRD.md` | Architecture, tech stack decisions, request lifecycle, non-functional requirements. |
| `context/03_BACKEND_SCHEMA.md` | Postgres tables, Redis key patterns, API request/response contracts, config.yaml format. |
| `context/04_APP_FLOW.md` | Exact step-by-step behavior for every flow (happy path, failover, rate limit, budget, streaming, admin, load test). |
| `context/05_UI_UX.md` | Grafana dashboard panel specs, error response shape, README structure. |
| `context/06_IMPLEMENTATION_PLAN.md` | Repo file structure, phase-by-phase file-touch map (what to create/modify/leave alone per phase), dependency introduction timing. |
| `context/07_CODING_STYLE.md` | Naming, function size, docstring format, testing, commit conventions. |
| `context/08_SECURITY.md` | Threat model, key handling, logging rules, what must never be logged or committed. |
| `context/09_DEPENDENCIES.md` | Exact approved dependency list and, critically, the explicit "do NOT add" list. |

If fewer than 9 files are present in `context/`, treat any missing doc as still authoritative in spirit — ask before inventing conventions that would normally be specified in a missing file, rather than guessing.

## Hard Rules (do not violate these regardless of what a specific task seems to ask for)

1. **Follow the current phase's file-touch map exactly** (`06_IMPLEMENTATION_PLAN.md`). Do not create or modify files listed under a later phase, and do not touch files marked "do not touch" for the current phase, even if it seems convenient.
2. **Provider list is fixed at 4**: Gemini, Claude, Groq, Ollama. Never add another provider (OpenAI, Cohere, Mistral, etc.) even if asked casually — confirm with Gaurav first if a request implies this.
3. **Dependency additions must appear in `09_DEPENDENCIES.md`.** If a task seems to need a package not listed there, stop and flag it rather than installing it — check the "Explicitly Do NOT Add" list first, since several likely-tempting packages (Celery, pre-built LLM gateway libraries, OpenAI SDK, Flask) are deliberately excluded.
4. **Never commit or log**: provider API keys (full or partial), team API key plaintext, full request/response content by default. See `08_SECURITY.md` section 3 for the exact logging allow/deny list.
5. **No raw SQL string interpolation.** SQLAlchemy ORM only, or parameterized queries if raw SQL is unavoidable.
6. **No synchronous/blocking calls in the request path** (`app/api/*.py` and anything it calls). Async only, per `07_CODING_STYLE.md`.
7. **Error responses must use the standardized error taxonomy** in `05_UI_UX.md` section 5 (`rate_limit_exceeded`, `budget_exceeded`, `provider_unavailable`, `invalid_api_key`, `invalid_request`, `internal_error`) — never return a bare unstructured 500.
8. **Migrations are additive, not edited.** Never hand-edit an already-applied Alembic migration; generate a new one for schema changes.
9. **Config values (timeouts, thresholds, model names) never hardcoded** in logic files — they belong in `app/config.py` or the `teams` / `team_model_access` tables.
10. **Do not refactor or "improve" working code from a completed phase** while implementing a later phase, unless a bug is found. If a bug is found in earlier-phase code, fix it minimally, add a regression test, and note it — don't use it as an excuse to restructure.

## When a Request Is Ambiguous or Seems to Expand Scope

Check `01_PRD.md` section 2 (non-goals) and `09_DEPENDENCIES.md`'s do-not-add list first. If the request conflicts with either, say so explicitly and ask for confirmation before proceeding — don't silently comply and don't silently refuse either.

## Current Phase

Track progress here manually as phases complete:
- [ ] Phase 1 — Unified Proxy Layer
- [ ] Phase 2 — Rate Limiting + Budget Enforcement
- [ ] Phase 3 — Fallback + Resilience
- [ ] Phase 4 — Observability
- [ ] Phase 5 — Load Test + Polish
- [ ] Secondary — Semantic Caching

Only work within the checked-off-as-current phase's scope unless explicitly told to jump ahead.