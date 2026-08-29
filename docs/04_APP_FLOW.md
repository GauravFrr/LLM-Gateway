# LLM Gateway — App Flow

## 1. Happy Path — Normal Request

1. Client sends `POST /v1/chat/completions` with team API key + tier `"balanced"`
2. Auth middleware resolves key → team_id (Redis cache hit, ~0.1ms)
3. Rate limit check passes (Redis Lua script, ~0.5ms)
4. Budget check: current spend is 40% of monthly budget → passes, no warning header
5. Model resolution: team's `balanced` tier → primary = Claude Sonnet
6. Circuit breaker check: Claude circuit is `closed` (healthy) → proceed
7. Request forwarded to Claude API
8. Claude responds successfully in 780ms
9. Response normalized to gateway's unified schema
10. Cost computed from token usage × pricing table: $0.0041
11. Async write to `request_logs`, Redis spend counter incremented
12. OTel span closed, Prometheus counters incremented (requests_total, tokens_total, cost_total)
13. Response returned to client with `provider: "claude"`, `was_fallback: false`

## 2. Failover Path — Primary Provider Down

1–6. Same as happy path, but at step 7, Claude API times out after 30s (or returns 503)
7. Gateway retries once on Claude with exponential backoff (500ms) — still fails
8. Failure recorded against Claude's circuit breaker counter in Redis
9. Gateway immediately routes to configured fallback for `balanced` tier → Gemini
10. Request forwarded to Gemini, succeeds
11. Response normalized and returned with `provider: "gemini"`, `was_fallback: true`
12. `request_logs` entry records both the failed Claude attempt AND the successful Gemini one (two rows, linked by the same `request_id`) — this is critical for the "forensics" story in the case study
13. If this is the 5th consecutive failure for Claude within the rolling window → circuit breaker transitions `closed` → `open`; an event is logged to `provider_health_events` and (if alerting is built) a Slack notification fires

## 3. Circuit Breaker Lifecycle

```
   [CLOSED] ──(5 consecutive failures)──▶ [OPEN]
      ▲                                      │
      │                                (cooldown timer,
      │                                 e.g. 30s elapses)
      │                                      ▼
      └──(1 success)── [HALF_OPEN] ◀─────────┘
              │
              └──(1 failure)──▶ back to [OPEN], reset cooldown
```
- **CLOSED:** normal operation, all requests go to this provider
- **OPEN:** all requests for this provider are skipped entirely and routed straight to fallback — no wasted timeout waiting
- **HALF_OPEN:** after cooldown, the *next single request* is allowed through as a test. Success → CLOSED. Failure → back to OPEN, cooldown resets.

## 4. Rate Limit Exceeded Path

1. Client sends request, team is already at their `rate_limit_rpm` cap
2. Redis Lua script atomically checks-and-rejects in a single round trip (no race condition between check and increment)
3. Gateway returns `429 Too Many Requests` with `Retry-After: <seconds>` header
4. Request is NOT forwarded to any provider, NOT logged as a provider call (logged as a `rate_limited` status row for team-level analytics only)
5. No cost incurred, no provider hit

## 5. Budget Exceeded Path

1. Request arrives, team's current-month spend (Redis counter) is at 100%+ of `monthly_budget_usd`
2. Gateway returns a custom `402`-style error: `{"error": "budget_exceeded", "current_spend": 50.12, "budget": 50.00}`
3. Request not forwarded. Logged as `budget_blocked`.
4. At 80% threshold (checked on every request under the cap), response still succeeds but includes header `X-Budget-Warning: 82%` — client can choose to surface this to their own users/logs

## 6. Streaming Request Flow

1. Client sends request with `"stream": true`
2. Steps 2–6 (auth, rate limit, budget, routing, circuit check) happen identically and synchronously BEFORE streaming starts — a request that will be rate-limited is rejected before any tokens stream, not mid-stream
3. Gateway opens a streamed connection to the provider and pipes chunks back to the client as SSE, accumulating token count as it goes
4. On stream completion (or client disconnect), final cost/token accounting is computed and logged — same as non-streaming, just deferred to stream-end
5. If the provider stream errors mid-way, the gateway CANNOT silently fall back mid-stream (partial output already sent to client) — it closes the stream with an error event and logs the partial failure. Fallback-on-stream-failure is a known limitation, documented as such, not hidden.

## 7. Admin Flow — Adding a New Team

1. Admin calls `POST /admin/teams` with name, budget, priority tier (requires separate admin API key, not a team key)
2. Gateway inserts row into `teams` table
3. Admin calls `POST /admin/teams/{id}/model-access` to configure tier→provider mappings and rate limits
4. Changes take effect immediately for new requests — no gateway restart or redeploy required (this is the "hot reload" requirement)
5. Existing Redis rate-limit counters for that team start fresh under the new limits (old counters simply expire naturally per their TTL)

## 8. Load Test Flow (Phase 5 validation)

1. k6 script simulates 5,000 concurrent requests across 3 demo teams with mixed tiers
2. Script deliberately includes a simulated provider outage window (achieved via a mock provider or by pointing one "provider" at a deliberately broken local endpoint mid-test)
3. Metrics captured: p50/p95/p99 latency, error rate, rate-limit accuracy (zero requests should exceed configured limits), fallback activation count during the outage window
4. Results feed directly into the portfolio case study numbers — report actual measured results, not the guide's target numbers, even if they differ
