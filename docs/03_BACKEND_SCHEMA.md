# LLM Gateway — Backend Schema

## 1. PostgreSQL Schema

### `teams`
| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | |
| name | TEXT | unique, human-readable |
| api_key_hash | TEXT | store hashed (SHA-256), never plaintext — see Security doc |
| monthly_budget_usd | NUMERIC(10,2) | |
| priority_tier | TEXT | `high` / `normal` / `low` — used for priority queueing |
| created_at | TIMESTAMPTZ | |
| is_active | BOOLEAN | soft-disable without deleting |

### `team_model_access`
| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | |
| team_id | UUID (FK → teams.id) | |
| logical_tier | TEXT | `fast` / `balanced` / `quality` |
| primary_provider | TEXT | `gemini` / `claude` / `groq` / `ollama` |
| primary_model | TEXT | e.g. `gemini-2.0-flash` |
| fallback_provider | TEXT | nullable |
| fallback_model | TEXT | nullable |
| rate_limit_rpm | INTEGER | requests/min for this team |
| rate_limit_tpm | INTEGER | tokens/min for this team |

### `request_logs`
| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | |
| team_id | UUID (FK) | |
| request_id | UUID | trace correlation ID, matches OTel span |
| logical_tier | TEXT | |
| provider_used | TEXT | actual provider that served it |
| model_used | TEXT | |
| was_fallback | BOOLEAN | |
| input_tokens | INTEGER | |
| output_tokens | INTEGER | |
| cost_usd | NUMERIC(10,6) | computed from pricing table |
| latency_ms | INTEGER | end-to-end, gateway-measured |
| gateway_overhead_ms | INTEGER | latency minus provider call time |
| status | TEXT | `success` / `failed` / `rate_limited` / `budget_blocked` |
| error_detail | TEXT | nullable |
| created_at | TIMESTAMPTZ | indexed, partitioned by month if volume grows |

### `provider_health_events`
| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | |
| provider | TEXT | |
| event_type | TEXT | `circuit_opened` / `circuit_half_open` / `circuit_closed` |
| reason | TEXT | e.g. "5 consecutive failures" |
| created_at | TIMESTAMPTZ | |

### `pricing_table`
| Column | Type | Notes |
|---|---|---|
| provider | TEXT | |
| model | TEXT | |
| input_cost_per_1k_tokens | NUMERIC(10,6) | |
| output_cost_per_1k_tokens | NUMERIC(10,6) | |
| updated_at | TIMESTAMPTZ | manually updated when providers change pricing — document this as a known manual-maintenance point, not automated |

## 2. Redis Key Schema

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `ratelimit:{team_id}:rpm` | Token bucket (custom via Lua) | 60s rolling | requests/min enforcement |
| `ratelimit:{team_id}:tpm` | Token bucket (custom via Lua) | 60s rolling | tokens/min enforcement |
| `budget:{team_id}:month:{yyyymm}` | Counter (float, cents) | expires end of month | running spend, synced periodically to Postgres |
| `circuit:{provider}:state` | String (`closed`/`open`/`half_open`) | none (persists until changed) | circuit breaker state |
| `circuit:{provider}:failures` | Counter | 60s rolling window | consecutive/recent failure count |
| `apikey:{key_hash}` | String (team_id) | 5 min | auth cache to avoid Postgres hit every request |
| `cache:{request_hash}` | String (response JSON) | configurable, default 1hr | semantic/exact cache layer (secondary goal) |

## 3. API Contract

### `POST /v1/chat/completions`
Request:
```json
{
  "tier": "balanced",
  "messages": [{"role": "user", "content": "..."}],
  "stream": false,
  "max_tokens": 1024
}
```
Response (normalized, regardless of underlying provider):
```json
{
  "id": "req_abc123",
  "provider": "claude",
  "model": "claude-sonnet-4-5",
  "was_fallback": false,
  "content": "...",
  "usage": {"input_tokens": 120, "output_tokens": 340, "cost_usd": 0.0041},
  "latency_ms": 812
}
```

### Admin API (separate router, requires admin key — see Security doc)
- `POST /admin/teams` — create team
- `PATCH /admin/teams/{id}` — update budget/limits
- `GET /admin/teams/{id}/usage` — spend breakdown
- `GET /admin/providers/health` — current circuit breaker states
- `POST /admin/providers/{provider}/reset-circuit` — manual override

### Observability endpoints
- `GET /metrics` — Prometheus scrape endpoint (no auth, internal network only — see Security doc)
- `GET /health` — liveness probe
- `GET /health/ready` — readiness probe (checks Redis + Postgres connectivity)

## 4. Config File (`config.yaml`, loaded into Postgres on startup)

```yaml
teams:
  - name: "team-search"
    monthly_budget_usd: 50.00
    priority_tier: "high"
    model_access:
      - tier: fast
        primary: {provider: groq, model: llama-3.1-8b-instant}
        fallback: {provider: gemini, model: gemini-2.0-flash}
        rate_limit_rpm: 100
        rate_limit_tpm: 50000
      - tier: quality
        primary: {provider: claude, model: claude-sonnet-4-5}
        fallback: {provider: gemini, model: gemini-2.0-pro}
        rate_limit_rpm: 20
        rate_limit_tpm: 20000
```
This file is the *seed*, not the runtime source of truth — after startup, changes go through the Admin API and are written to Postgres directly. Re-running the loader is idempotent (upsert by team name).
