from prometheus_client import Counter, Histogram, Gauge

# 1. Gateway logic overhead time (Histogram)
GATEWAY_OVERHEAD = Histogram(
    "llm_gateway_overhead_seconds",
    "Gateway overhead logic execution time in seconds",
    ["team_id", "logical_tier"]
)

# 2. Total request latency (Histogram)
REQUEST_LATENCY = Histogram(
    "llm_gateway_request_duration_seconds",
    "Total end-to-end request latency in seconds",
    ["provider", "model", "status_code", "team_id", "was_fallback"]
)

# 3. Requests count (Counter)
REQUEST_COUNT = Counter(
    "llm_gateway_requests_total",
    "Total gateway requests count",
    ["provider", "model", "status_code", "team_id", "was_fallback"]
)

# 4. Circuit breaker state gauge (0 = closed, 1 = half-open, 2 = open)
CIRCUIT_STATE = Gauge(
    "llm_gateway_circuit_breaker_state",
    "Circuit breaker state status (0=closed, 1=half-open, 2=open)",
    ["provider"]
)

# 5. Fallback requests count (Counter)
FALLBACK_COUNT = Counter(
    "llm_gateway_fallback_requests_total",
    "Total fallback requests count",
    ["team_id", "logical_tier", "fallback_provider"]
)

# 6. Team Spend (Counter)
TEAM_SPEND = Counter(
    "llm_gateway_spend_usd_total",
    "Total spend in USD",
    ["team_id", "team_name", "provider"]
)

# 7. Token throughput count (Counter)
TOKEN_COUNT = Counter(
    "llm_gateway_tokens_total",
    "Total tokens processed",
    ["team_id", "provider", "token_type"]
)

# 8. Rate limit rejection count (Counter)
RATE_LIMIT_REJECTION = Counter(
    "llm_gateway_rate_limit_rejections_total",
    "Total rate limit rejections",
    ["team_id", "team_name", "logical_tier", "rejection_type"]
)

# 9. Team monthly budget usage ratio (Gauge: spend/budget)
TEAM_BUDGET_USAGE = Gauge(
    "llm_gateway_team_budget_usage_ratio",
    "Ratio of team's current monthly spend divided by their budget",
    ["team_id", "team_name"]
)
