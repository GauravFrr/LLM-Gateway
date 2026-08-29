# LLM Gateway — Product Requirements Document (PRD)

## 1. Overview

**Product name:** LLM Gateway (working name — finalize before launch)

**One-line description:** A production-grade API gateway that sits between applications and multiple LLM providers, enforcing per-team rate limits and budgets, automatically failing over on provider outages, and giving unified observability across every LLM call.

**Why this exists:** Teams building on LLMs today call providers (OpenAI, Anthropic, Gemini, Groq, local models) directly from application code. This causes four recurring problems in production:
- No cost visibility or control — any engineer/team can silently run up large bills
- No resilience — a provider outage or rate-limit directly breaks the product
- No fairness — one team/service can starve others of shared quota
- No observability — failures and slowness are invisible until a user complains

This project builds the infrastructure layer that solves all four, as a portfolio piece demonstrating production systems engineering applied to AI infrastructure.

## 2. Goals

### Primary goals (must ship)
1. Single unified API that proxies to multiple LLM providers with one consistent request/response contract
2. Per-team rate limiting (requests/min and tokens/min)
3. Per-team budget enforcement (soft warn at 80%, hard block at 100%)
4. Automatic fallback to a secondary provider/model on primary failure or rate-limit
5. Full request-level observability: latency, cost, token usage, provider, success/failure, logged and queryable
6. Admin API to view/update team configs, limits, and budgets without redeploying

### Secondary goals (build if time allows, documented as roadmap if not)
7. Circuit breaker pattern per provider (auto-disable a failing provider temporarily)
8. Semantic caching layer to reduce duplicate LLM spend
9. Grafana dashboards for live metrics
10. Load test proving throughput and latency overhead claims

### Explicit non-goals (out of scope — do not build)
- Fine-tuning or model training of any kind
- A frontend chat UI for end users (this is infrastructure, not a chat app)
- Multi-tenant billing/invoicing (budget *tracking*, not billing/payments)
- User authentication/SSO system (API-key based auth only, see TRD)
- Prompt management / prompt versioning UI (that's Project 1's territory, not this one)
- Support for every possible LLM provider — cap at 4: Gemini, Claude (Anthropic), Groq, and one local model via Ollama, matching Gaurav's existing stack

## 3. Target users (for the portfolio narrative)

Framed as if built for a mid-size company's platform team:
- **Backend engineers** integrating LLM calls into product features — they call the gateway, not providers directly
- **Team leads / eng managers** who need budget visibility per team
- **Platform/infra on-call** who need to know instantly when a provider degrades

## 4. Core user stories

1. As a backend engineer, I send a request to `/v1/chat/completions` with my team's API key and get a response, without knowing or caring which underlying provider served it.
2. As a backend engineer, if my primary model is down, my request still succeeds via automatic fallback — I don't see an error, I see (at worst) added latency.
3. As a team lead, I can see how much my team has spent this month, this week, today, broken down by model.
4. As a team lead, if my team is about to exceed budget, I get a warning before it happens, and a hard stop (with a clear error, not a silent failure) if it's exceeded.
5. As platform on-call, I can see in one place: which provider is unhealthy right now, what the fallback behavior did, and what the error rate/latency looks like per provider.
6. As an admin, I can add a new team, set its rate limit and budget, and enable/disable specific models for it — via API, without a deploy.

## 5. Success metrics (for the portfolio case study)

- Gateway overhead added per request (target: report the real measured number — guide targets <10ms, but report actual measured result honestly, even if it's higher)
- % of requests successfully failed-over during a simulated provider outage (target: 100% of retryable failures recovered)
- Rate limiting accuracy under concurrent load (target: 0 requests allowed past the configured limit during load test)
- Cost visibility: dashboard shows accurate $ spend within known token-pricing margins

## 6. Constraints

- Solo build, part-time (2–3 hrs/day)
- Must run fully locally via `docker-compose up` for demo purposes — no paid cloud infra required to demo it
- Must work with real provider API keys (Gemini, Claude, Groq) for the demo, plus Ollama for a "local/free" option
- Full scope (including secondary goals) is committed to per Gaurav's decision — timeline is not the limiting factor, but each phase must be independently working and demoable before starting the next (no half-finished layers)

## 7. Assumptions

- Gaurav already has API access to Gemini, Claude, and Groq (used in Retryv/Scoutr)
- Ollama will be installed locally for the "local model" fallback tier
- PostgreSQL will be used for durable config/audit data (team configs, spend history) — Redis is for hot-path rate limiting only, not source of truth
