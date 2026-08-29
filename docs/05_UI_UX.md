# LLM Gateway — UI/UX

This project has **no end-user-facing frontend** by design (see PRD non-goals). "UI/UX" here means the three surfaces a human actually interacts with: Grafana dashboards, the Admin API (via Swagger/OpenAPI, no custom frontend needed), and the demo walkthrough experience.

## 1. Grafana Dashboards (3 boards, pre-provisioned via docker-compose)

### Board 1: Operations
- **Panel:** Request rate (req/s) — time series, split by provider
- **Panel:** Error rate % — time series, red threshold line at 5%
- **Panel:** Latency percentiles (p50/p95/p99) — time series
- **Panel:** Circuit breaker state per provider — status panel (green/yellow/red = closed/half-open/open)
- **Panel:** Fallback activation rate — time series, annotated with circuit breaker state changes

### Board 2: Business
- **Panel:** Spend today / this week / this month — big number stat panels
- **Panel:** Spend by team — bar chart
- **Panel:** Spend by provider — pie chart
- **Panel:** Teams approaching budget (≥80%) — table, red-highlighted rows
- **Panel:** Cost saved via cache hits (if semantic caching is built) — big number, "$X saved"

### Board 3: Performance
- **Panel:** Gateway overhead (ms) — time series, separate line from total latency, this is the "<10ms" claim visualized
- **Panel:** Token throughput (tokens/sec) — time series
- **Panel:** Rate-limit rejections over time — time series, split by team
- **Panel:** Provider health check latency — time series

**Design note:** these are the panels that go directly into the demo Loom recording and the case study screenshots — build these panels to look clean and readable at a glance, since they ARE the primary "UI" a reviewer will see.

## 2. Admin API "UI" — Swagger/OpenAPI

FastAPI auto-generates interactive docs at `/docs` (Swagger UI) and `/redoc`. This is the actual interface used to demo admin operations (creating a team, adjusting a budget, resetting a circuit breaker) in the walkthrough video — no custom admin frontend needs to be built. Keep endpoint names, descriptions, and example request bodies clean in the FastAPI route definitions, since this becomes the de facto UI.

## 3. CLI / curl Demo Script

For the portfolio demo, prepare a `demo.sh` script with clearly commented curl commands that walks through:
1. Creating a team
2. Making a successful request
3. Triggering a rate limit (rapid-fire requests)
4. Triggering a simulated provider failure → showing fallback in the response `was_fallback: true`
5. Checking spend via `/admin/teams/{id}/usage`

This script doubles as living documentation and as the literal script followed in the Loom recording.

## 4. README as UX Surface

Since this is a developer tool, the README IS the product's UX for anyone evaluating it (recruiters, other engineers). Structure:
1. One-paragraph "what is this and why" (from PRD section 1)
2. Architecture diagram (from TRD section 1)
3. Quickstart: `docker-compose up`, seed script, first curl request — must work in under 5 minutes for someone cloning fresh
4. Link to demo video
5. "Design decisions" section — the interview-talking-points, framed as engineering rationale (why Redis for rate limiting, why circuit breakers over simple retries, etc.)
6. Known limitations section (streaming fallback limitation, manual pricing table updates, etc.) — being upfront about limitations is a stronger signal than pretending there are none

## 5. Error Message UX (for API consumers)

Every error response follows one consistent shape so client developers never have to guess:
```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Team 'team-search' exceeded 100 req/min limit.",
    "retry_after_seconds": 12,
    "request_id": "req_abc123"
  }
}
```
Error codes to standardize: `rate_limit_exceeded`, `budget_exceeded`, `provider_unavailable`, `invalid_api_key`, `invalid_request`, `internal_error`. Never return a bare `500` with no code/context — every failure mode listed in the App Flow doc must map to one of these named codes.
