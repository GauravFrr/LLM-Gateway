import datetime
import logging
import sys
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.health import router as health_router
from app.api.v1.admin import router as admin_router
from app.api.v1.chat import router as chat_router
from app.config import settings
from app.db.session import SessionLocal, redis_pool
from app.models.db import Team
from app.observability.tracing import setup_tracing


# Configure structlog
def configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


async def warm_prometheus_metrics():
    logger = structlog.get_logger()
    logger.info("warming_prometheus_metrics_start")

    redis_client = redis_async.Redis(connection_pool=redis_pool)
    from app.observability.metrics import CIRCUIT_STATE, TEAM_BUDGET_USAGE

    providers = ["gemini", "claude", "groq", "ollama"]
    for p in providers:
        state_key = f"circuit:{p}:state"
        try:
            state = await redis_client.get(state_key)
            if state:
                state_val = state.decode() if isinstance(state, bytes) else state
                val = 0 if state_val == "closed" else (1 if state_val == "half_open" else 2)
                CIRCUIT_STATE.labels(provider=p).set(val)
            else:
                CIRCUIT_STATE.labels(provider=p).set(0)
        except Exception as e:
            logger.error("warm_cb_state_failed", provider=p, error=str(e))
            CIRCUIT_STATE.labels(provider=p).set(0)

    # Warm team budget usages
    async with SessionLocal() as db:
        try:
            result = await db.execute(select(Team))
            teams = result.scalars().all()

            yyyymm = datetime.datetime.utcnow().strftime("%Y%m")
            for team in teams:
                budget_key = f"budget:{team.id}:month:{yyyymm}"
                try:
                    spend_str = await redis_client.get(budget_key)
                    current_spend = 0.0
                    if spend_str:
                        current_spend = float(spend_str) / 100.0

                    budget = float(team.monthly_budget_usd)
                    ratio = current_spend / budget if budget > 0 else 0.0
                    TEAM_BUDGET_USAGE.labels(team_id=str(team.id), team_name=team.name).set(ratio)
                except Exception as ex:
                    logger.error("warm_team_budget_failed", team_id=team.id, error=str(ex))
        except Exception as e:
            logger.error("warm_prometheus_metrics_db_failed", error=str(e))
        finally:
            await db.close()
            await redis_client.close()

    logger.info("warming_prometheus_metrics_complete")


import os


def write_prometheus_config():
    logger = structlog.get_logger()
    scrape_interval = os.getenv("PROMETHEUS_SCRAPE_INTERVAL", "5s")
    logger.info("writing_prometheus_config", scrape_interval=scrape_interval)
    content = f"""global:
  scrape_interval: {scrape_interval}
  evaluation_interval: {scrape_interval}

scrape_configs:
  - job_name: 'llm-gateway'
    static_configs:
      - targets: ['gateway:8000', 'host.docker.internal:8000']
"""
    try:
        with open("prometheus.yml", "w") as f:
            f.write(content)
    except Exception as e:
        logger.error("failed_to_write_prometheus_yml", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    configure_logging()
    write_prometheus_config()
    logger = structlog.get_logger()
    logger.info("gateway_started", host=settings.HOST, port=settings.PORT)
    await warm_prometheus_metrics()
    yield
    # Shutdown tasks
    logger.info("gateway_shutdown")


app = FastAPI(
    title="LLM Gateway",
    description="Unified API Proxy for LLM Providers (Gemini, Claude, Groq, Ollama)",
    version="1.0.0",
    lifespan=lifespan,
)

# Set up OTel tracing instrumentor
setup_tracing(app)


# Request ID Middleware
@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.request_id = request_id

    # Bind request_id to structlog context variables for automatic correlation
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Register routes
app.include_router(chat_router, prefix="/v1/chat", tags=["chat"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(health_router, tags=["health"])


# Global unhandled exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger = structlog.get_logger()
    logger.exception("unhandled_internal_error", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected gateway error occurred.",
            }
        },
    )
