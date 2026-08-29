# LLM Gateway — Dependencies Reference

## 1. Core Dependencies (use these, pinned versions in requirements.txt)

| Package | Purpose | Introduced in Phase |
|---|---|---|
| fastapi | API framework | 1 |
| uvicorn[standard] | ASGI server | 1 |
| httpx | Async HTTP client for provider calls | 1 |
| pydantic / pydantic-settings | Schema validation + config | 1 |
| google-generativeai | Gemini SDK | 1 |
| anthropic | Claude SDK | 1 |
| groq | Groq SDK | 1 |
| redis (async client, `redis.asyncio`) | Rate limiting, circuit breaker state, cache | 2 |
| sqlalchemy[asyncio] | ORM | 2 |
| asyncpg | Async Postgres driver | 2 |
| alembic | DB migrations | 2 |
| tenacity | Retry/backoff logic | 3 |
| opentelemetry-sdk, opentelemetry-instrumentation-fastapi | Tracing | 4 |
| prometheus-client | Metrics | 4 |
| structlog | Structured logging | 1 (used throughout) |

## 2. Dev/Test Dependencies

| Package | Purpose |
|---|---|
| pytest, pytest-asyncio | Test runner |
| respx | Mock httpx calls in tests |
| ruff | Lint + format |
| gitleaks (not pip, standalone binary) | Secret scanning before commits |
| pip-audit | Dependency CVE scanning |

## 3. Infra (not pip packages — docker images)

| Service | Image | Purpose |
|---|---|---|
| Redis | `redis:7-alpine` | Rate limiting, circuit breaker, cache |
| PostgreSQL | `postgres:15-alpine` | Durable config, logs, audit |
| Prometheus | `prom/prometheus` | Metrics scraping |
| Grafana | `grafana/grafana` | Dashboards |
| Ollama | `ollama/ollama` | Local model provider |

## 4. Explicitly Do NOT Add

- **Celery / RabbitMQ / any message queue** — async FastAPI background tasks handle the async-logging use case; a queue is scope creep for this project's actual requirements
- **Any "AI gateway" pip package** (e.g. pre-built LLM proxy libraries) — the entire point of this project is to build the routing/fallback/rate-limiting logic yourself; using a pre-built one defeats the portfolio purpose
- **OpenAI SDK** — not in the 4-provider list; do not add "just in case," it's explicit scope creep per the PRD
- **Django / Flask** — FastAPI is the committed choice; do not mix frameworks
- **Any synchronous ORM usage** (plain `psycopg2` without async) — breaks the async-all-the-way-down requirement in the coding style doc
- **Cohere, Mistral, or other extra providers** — capped at 4 per TRD section 3
- **A custom-rolled JWT/session auth system** — API-key auth only, per PRD non-goals; don't add `python-jose` or session middleware, it's unnecessary complexity for this scope

## 5. Version Pinning Policy

All dependencies pinned to exact versions in `requirements.txt` (or via `pyproject.toml` + lockfile if using `uv`/`poetry`). Run `pip-audit` before any version bump, and bump deliberately, not via a blanket `pip install --upgrade` — a version bump is a commit of its own (`chore: bump httpx to x.y.z`), never bundled silently into a feature commit.
