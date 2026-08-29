# LLM Gateway — Implementation Plan

## 1. Repository Structure (target end-state)

```
llm-gateway/
├── app/
│   ├── main.py                    # FastAPI app entrypoint
│   ├── config.py                  # settings via pydantic-settings, reads .env
│   ├── dependencies.py            # shared FastAPI dependencies (auth, db session)
│   ├── api/
│   │   ├── chat.py                # POST /v1/chat/completions
│   │   ├── admin.py               # /admin/* routes
│   │   └── health.py              # /health, /health/ready, /metrics
│   ├── core/
│   │   ├── auth.py                # API key validation + Redis cache lookup
│   │   ├── rate_limiter.py        # Redis Lua token bucket logic
│   │   ├── budget.py              # budget check + spend tracking
│   │   ├── router.py              # tier → provider/model resolution
│   │   └── circuit_breaker.py     # circuit breaker state machine
│   ├── providers/
│   │   ├── base.py                # abstract Provider interface
│   │   ├── gemini.py
│   │   ├── claude.py
│   │   ├── groq.py
│   │   └── ollama.py
│   ├── models/
│   │   ├── db.py                  # SQLAlchemy models (teams, logs, etc.)
│   │   └── schemas.py             # Pydantic request/response models
│   ├── observability/
│   │   ├── tracing.py             # OTel setup
│   │   └── metrics.py             # Prometheus counters/histograms
│   └── db/
│       ├── migrations/            # Alembic migrations
│       └── seed.py                # loads config.yaml into Postgres
├── tests/
│   ├── unit/
│   ├── integration/
│   └── load/                      # k6 scripts
├── grafana/
│   ├── dashboards/                # JSON dashboard definitions
│   └── provisioning/
├── docker-compose.yml
├── Dockerfile
├── config.yaml                    # seed config
├── .env.example
├── demo.sh
└── README.md
```

## 2. Phase-by-Phase Plan with File-Touch Map

Each phase must end with a working, demoable slice — do not start the next phase's files until the current phase's files pass their own tests.

### Phase 1: Unified Proxy Layer
**Goal:** A request can hit the gateway and get a real response from at least one provider, unauthenticated, no rate limiting yet.

**Files to create:**
- `app/main.py`, `app/config.py`
- `app/providers/base.py`, `app/providers/gemini.py`, `app/providers/claude.py`, `app/providers/groq.py`, `app/providers/ollama.py`
- `app/models/schemas.py` (request/response contracts)
- `app/api/chat.py` (basic version, no auth/rate-limit yet)
- `Dockerfile`, `docker-compose.yml` (gateway service only)

**Do not touch yet:** anything in `app/core/` (auth, rate_limiter, budget, circuit_breaker) — those are Phase 2/3. Do not create Postgres models yet — Phase 1 can hardcode one team for testing.

**Dependencies to add:** `fastapi`, `uvicorn`, `httpx`, `pydantic`, `google-generativeai`, `anthropic`, `groq`, `python-dotenv`

### Phase 2: Rate Limiting + Budget Enforcement
**Goal:** Requests are authenticated against real team records, rate-limited, and budget-checked.

**Files to create:**
- `app/core/auth.py`, `app/core/rate_limiter.py`, `app/core/budget.py`
- `app/models/db.py` (teams, team_model_access tables)
- `app/db/migrations/` (first Alembic migration)
- `app/db/seed.py`, `config.yaml`
- `app/api/admin.py` (basic team CRUD)
- Redis Lua scripts under `app/core/lua/` (token_bucket.lua)

**Files modified (not created fresh):**
- `app/api/chat.py` — add auth + rate limit + budget dependency chain
- `docker-compose.yml` — add Redis, Postgres services

**Do not touch:** `app/providers/*` (already working from Phase 1, leave as-is unless a bug is found). Do not touch circuit breaker or observability files — not built yet.

**Dependencies to add:** `redis` (async client), `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic-settings`

### Phase 3: Fallback + Resilience
**Goal:** Provider failures trigger automatic fallback; repeated failures open a circuit breaker.

**Files to create:**
- `app/core/circuit_breaker.py`, `app/core/router.py` (tier resolution + fallback logic)
- `app/models/db.py` — MODIFY to add `provider_health_events` table (new migration, not a new file for the table itself, but a new migration file)

**Files modified:**
- `app/api/chat.py` — wrap provider calls in retry/fallback/circuit-breaker logic
- `app/providers/base.py` — add a standard exception hierarchy (`RetryableError`, `NonRetryableError`) so `router.py` can decide fallback behavior generically

**Do not touch:** rate_limiter.py, budget.py, auth.py — these are done and stable from Phase 2.

**Dependencies to add:** `tenacity` (for retry/backoff — don't hand-roll this, it's a well-tested library and hand-rolling backoff logic is a common source of subtle bugs)

### Phase 4: Observability
**Goal:** Every request emits traces/metrics; Grafana dashboards are live and populated.

**Files to create:**
- `app/observability/tracing.py`, `app/observability/metrics.py`
- `grafana/dashboards/*.json` (3 dashboards per UI/UX doc)
- `grafana/provisioning/` config files
- `app/api/health.py` (`/metrics` endpoint)

**Files modified:**
- `app/main.py` — wire in OTel instrumentation middleware
- `app/api/chat.py` — add span creation and metric emission at each stage
- `docker-compose.yml` — add Prometheus, Grafana services

**Do not touch:** anything in `app/core/` logic itself — observability wraps existing logic, it doesn't change routing/rate-limit/fallback decisions.

**Dependencies to add:** `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, `prometheus-client`

### Phase 5: Load Test + Polish
**Goal:** Proven numbers, demo-ready.

**Files to create:**
- `tests/load/gateway_load_test.js` (k6 script)
- `demo.sh`
- `README.md` (final version)

**Do not touch:** application code should not change during this phase unless the load test surfaces an actual bug — this phase is validation, not feature work. If a bug is found, fix it minimally and re-run, don't refactor.

### (Secondary, if pursued) Semantic Caching
**Files to create:** `app/core/cache.py`
**Files modified:** `app/api/chat.py` (cache check before provider call, cache write after)
**Do not touch:** rate limiting/budget — cache check happens AFTER rate limit/budget checks pass, so cached responses still count against rate limits (a cache hit still "used" the request slot) but should NOT count against budget spend (no actual token cost incurred) — this is a specific, easy-to-get-wrong detail, document it in code comments where the cache check sits.

## 3. General "Do Not Touch" Rules (apply across all phases)

- `db/migrations/` — never hand-edit a migration file after it's been applied; always generate a new migration for schema changes, even small ones
- `.env` — never commit this file; only `.env.example` with placeholder values goes in git
- `pricing_table` data — this is manually maintained; do not attempt to auto-fetch pricing from providers (no stable API for this exists across all 4 providers)
- Provider SDK internals — never patch/monkeypatch vendored SDK code; if a provider SDK has a bug or missing feature, work around it in `app/providers/{provider}.py`, not by editing installed package files

## 4. Dependency Policy

**Use:** well-maintained, widely-adopted libraries for anything security- or correctness-critical (retry logic → `tenacity`, not hand-rolled; rate limiting primitives → Redis Lua scripts, well-understood pattern, not a random pip package claiming to do "AI rate limiting").

**Avoid:** any pip package with <500 GitHub stars or no commits in 12+ months for anything touching auth, rate limiting, or security. Avoid adding a message queue (Celery/RabbitMQ) — the guide doesn't need it and it's scope creep; async FastAPI background tasks are sufficient for the async logging use case.

**Never add "just in case":** every new dependency added should map to a specific requirement in this doc or the TRD. If during coding you find yourself wanting to `pip install` something not listed here, stop and ask whether it's actually needed or whether existing tools (stdlib, already-added deps) solve it.
