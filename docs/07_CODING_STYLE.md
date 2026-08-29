# LLM Gateway — Coding Style Guide

## 1. General Python Style
- Follow PEP 8, enforced via `ruff` (linter + formatter, replaces black+flake8+isort in one tool)
- Type hints on every function signature — this is a systems project, types document contracts between layers (auth → rate limiter → router → provider)
- No bare `except:` — always catch specific exceptions; use the custom exception hierarchy from `providers/base.py` for provider errors
- Async all the way down in the request path — no blocking calls (`requests` library, sync DB calls) inside any function called from `app/api/chat.py`. Blocking calls are only acceptable in startup/seed scripts.

## 2. Project-Specific Conventions

### Naming
- Provider modules named after the provider exactly (`gemini.py`, `claude.py`) — no cleverness
- Redis keys always follow `{namespace}:{id}:{field}` pattern (see Backend Schema doc) — never invent a new pattern ad hoc
- Error codes are `snake_case` strings matching the UI/UX doc's error taxonomy exactly — don't introduce a new error code without adding it to that doc first

### Function size
- Route handlers in `app/api/*.py` should be thin — orchestration only (call auth, call rate limiter, call router, call provider, return). Business logic lives in `app/core/` and `app/providers/`, not in route handlers. A route handler over ~30 lines is a signal that logic should be extracted.

### Configuration
- No hardcoded values (timeouts, thresholds, model names) in logic files — everything configurable lives in `app/config.py` (env-based) or the `teams`/`team_model_access` tables (per-team). If you catch yourself writing a magic number in `router.py` or `circuit_breaker.py`, it belongs in config.

### Logging
- Structured logging only (`structlog` or stdlib `logging` with a JSON formatter) — every log line includes `request_id` when available, for correlation with traces
- Never log full API keys, even in debug mode — log key hash prefix only (first 8 chars of hash) — see Security doc

## 3. Docstring Convention

Every public function in `app/core/` and `app/providers/` gets a docstring covering: what it does, what it does NOT do (important for functions with subtle scope like `budget.py`'s check-vs-deduct distinction), and what exceptions it can raise.

Example:
```python
async def check_rate_limit(team_id: str, tokens_estimate: int) -> RateLimitResult:
    """
    Atomically checks (does not deduct) whether this team has capacity
    under both RPM and TPM limits. Deduction happens separately in
    record_request() after the provider call succeeds, since token
    count is only known precisely after the response returns.

    Raises:
        RedisConnectionError: if Redis is unreachable (treated as
        fail-open in Phase 2, revisit for fail-closed in production
        hardening — documented tradeoff, see Security doc).
    """
```

## 4. Testing Conventions
- Unit tests for every function in `app/core/` — these are pure logic, should be fast and not require Docker
- Integration tests use a real (test) Redis + Postgres via docker-compose, spun up in CI
- Provider calls in tests are always mocked (`respx` for httpx mocking) — never hit real provider APIs in automated tests, only in the manual `demo.sh` walkthrough
- Every bug found during load testing (Phase 5) gets a regression test added before the fix is considered done — same discipline as documented for Retryv's bug-catching narrative

## 5. Commit Style
- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`) — makes the git history itself part of the portfolio artifact reviewers may look at
- One phase = multiple commits, but each commit should leave the app in a working state (no "WIP broken" commits on main — use a branch per phase if needed, merge when the phase's demo works)

## 6. What NOT to Optimize For
- Do not chase micro-optimizations (e.g., hand-rolled connection pooling tweaks) before the load test in Phase 5 reveals an actual bottleneck — premature optimization here just eats build time better spent on correctness of rate limiting/fallback logic, which is the actual point of the project
- Do not build a plugin system for "future providers" — YAGNI; four providers, hardcoded provider modules per the Implementation Plan, is correct for this project's scope
