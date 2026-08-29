# LLM Gateway — Technical Requirements Document (TRD)

## 1. Architecture Overview

```
                        ┌─────────────────────────────────────────┐
                        │              Client Apps                  │
                        │  (send requests with Team API Key)        │
                        └────────────────────┬──────────────────────┘
                                             │ HTTPS
                                             ▼
                        ┌─────────────────────────────────────────┐
                        │            LLM Gateway (FastAPI)          │
                        │  ┌───────────────────────────────────┐   │
                        │  │ 1. Auth Middleware (API key lookup) │   │
                        │  │ 2. Rate Limit Check (Redis)         │   │
                        │  │ 3. Budget Check (Redis + Postgres)  │   │
                        │  │ 4. Provider Router                  │   │
                        │  │ 5. Circuit Breaker Check             │   │
                        │  │ 6. Request Forwarder (httpx async)   │   │
                        │  │ 7. Response Normalizer               │   │
                        │  │ 8. Telemetry Emitter (OTel)          │   │
                        │  └───────────────────────────────────┘   │
                        └───┬───────┬───────┬───────┬───────────────┘
                            │       │       │       │
                    ┌───────▼┐  ┌───▼────┐ ┌▼──────┐ ┌▼────────┐
                    │ Gemini │  │ Claude │ │ Groq  │ │ Ollama  │
                    │  API   │  │  API   │ │  API  │ │ (local) │
                    └────────┘  └────────┘ └───────┘ └─────────┘

        Supporting infra (all in docker-compose):
        - Redis        → rate limit counters, circuit breaker state, cache
        - PostgreSQL   → team configs, budgets, audit log, spend history
        - Prometheus   → metrics scraping
        - Grafana      → dashboards
        - OTel Collector → trace collection (optional, can log direct to stdout/Jaeger)
```

## 2. Tech Stack (final decisions)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Matches existing stack (Retryv, Scoutr, RankFuse); async support mature enough for this |
| API framework | FastAPI | Async-native, matches existing stack, auto OpenAPI docs |
| HTTP client (outbound) | httpx (async) | Async-compatible with FastAPI, supports streaming |
| Rate limiting store | Redis 7+ | Sub-ms atomic operations via Lua scripts, distributed-safe |
| Durable storage | PostgreSQL 15+ | Team configs, budgets, audit trail — matches existing stack (pgvector already in use elsewhere, though not needed here) |
| ORM | SQLAlchemy 2.0 (async) + Alembic | Migrations needed since schema will evolve across phases |
| Config format | YAML (loaded into Postgres on startup, hot-reloadable via admin API) | Human-editable defaults, but runtime source of truth is Postgres, not the file |
| Observability | OpenTelemetry SDK + Prometheus client | Industry standard, matches guide spec |
| Dashboards | Grafana (pre-provisioned via docker-compose) | No manual dashboard setup required for demo |
| Containerization | Docker + docker-compose | One-command full-stack demo |
| Testing | pytest + pytest-asyncio + httpx test client | Standard for FastAPI projects |
| Load testing | Locust or k6 | k6 preferred — better async/streaming support for this use case |

## 3. Provider Support (final list — do not expand)

1. **Google Gemini** (via `google-generativeai` SDK or REST)
2. **Anthropic Claude** (via `anthropic` SDK)
3. **Groq** (via `groq` SDK, OpenAI-compatible)
4. **Ollama** (local, via REST — llama3 or similar small model as the "free/local" tier)

Do not add OpenAI, Cohere, Mistral, or any other provider — four is sufficient to prove the pattern and keeps scope bounded. If asked "should we add provider X" during build, the answer is no unless it replaces one of the four.

## 4. Request Lifecycle (detailed)

1. **Ingress:** Request hits `POST /v1/chat/completions` with header `Authorization: Bearer <team_api_key>`
2. **Auth:** Middleware looks up `team_api_key` → `team_id` (Redis cache, falls back to Postgres on miss, cache result for 5 min)
3. **Rate limit check:** Token bucket check in Redis (Lua script, atomic) using `team_id`. If exceeded → `429` with `Retry-After` header. No forwarding happens.
4. **Budget check:** Read current-month spend for `team_id` from Redis counter (synced from Postgres). If ≥100% → `402 Payment Required`-style custom error. If ≥80% → proceed but flag `X-Budget-Warning: true` header on response.
5. **Model resolution:** Request specifies a logical model name (e.g. `"fast"`, `"balanced"`, `"quality"` — abstracted tiers) OR a specific provider/model. Gateway resolves to an actual provider+model per the team's config and current health status.
6. **Circuit breaker check:** If the resolved provider's circuit is OPEN, skip directly to fallback provider for that tier.
7. **Forward request:** Async HTTP call to the provider via httpx, with configured timeout (default 30s, configurable per model).
8. **On success:** Normalize response to unified schema (see Backend Schema doc), compute cost from token usage × provider pricing table, log to Postgres (async, non-blocking), update Redis spend counter, emit OTel span + Prometheus metrics, return to client.
9. **On failure (retryable — timeout, 5xx, rate-limited by provider):** Retry once with exponential backoff (base 500ms) on the SAME provider if the error suggests transience; if it fails again OR the error is non-retryable, mark a failure against that provider's circuit breaker and immediately try the configured fallback provider for that tier. Log the failover event.
10. **On failure (fallback also fails):** Return a clear `503` error to the client with which providers were attempted (for debuggability) — never a silent/generic 500.
11. **Streaming requests:** Steps 3–6 identical. Step 7 becomes a streamed passthrough (SSE), with token/cost accounting done incrementally as chunks arrive, finalized when the stream closes.

## 5. Non-Functional Requirements

- **Latency overhead:** Gateway logic (auth + rate limit + budget check + routing decision, excluding actual provider call time) should be measured and reported honestly. Target <10ms per guide; report the real number.
- **Availability:** Gateway itself must not become a single point of failure worse than calling providers directly — this is why circuit breakers and fast-fail on rate limits matter (fail fast, don't queue indefinitely).
- **Consistency:** Rate limit and budget checks must be atomic under concurrent requests (this is why Redis + Lua scripts, not read-then-write from app code).
- **Auditability:** Every request must be logged with enough detail to answer "why did this fail / what did it cost" after the fact — no silent drops.

## 6. Deployment Model (for demo purposes)

- `docker-compose up` brings up: gateway, Redis, Postgres, Prometheus, Grafana (pre-provisioned dashboards), Ollama.
- Seed script creates 2–3 demo teams with different limits/budgets for the walkthrough video.
- `.env.example` documents required provider API keys; real keys never committed (see Security doc).
