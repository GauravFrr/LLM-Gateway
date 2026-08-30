import datetime

import redis.asyncio as redis_async
import structlog
from fastapi import Depends, HTTPException, Request

from app.core.auth import require_auth
from app.db.session import get_redis
from app.models.db import Team

logger = structlog.get_logger()


async def check_budget(
    request: Request, team: Team = Depends(require_auth), redis_client: redis_async.Redis = Depends(get_redis)
):
    """
    Enforces monthly budget limits.
    Soft warning at 80% (adds request state flag), hard block at 100% (raises 402).
    """
    request_id = getattr(request.state, "request_id", "req_unknown")
    yyyymm = datetime.datetime.utcnow().strftime("%Y%m")
    budget_key = f"budget:{team.id}:month:{yyyymm}"

    try:
        spend_cents_str = await redis_client.get(budget_key)
        if spend_cents_str is None:
            # First request of the month: initialize in Redis
            # In Phase 3, we would load from Postgres request_logs, but in Phase 2 we default to 0.
            await redis_client.setnx(budget_key, 0.0)
            # Expire at the end of the month plus 2 days
            await redis_client.expire(budget_key, 32 * 24 * 3600)
            spend_cents = 0.0
        else:
            spend_cents = float(spend_cents_str)
    except Exception as e:
        logger.error("redis_error_in_budget_check", error=str(e), request_id=request_id)
        # Fail-open design on Redis failure as per 08_SECURITY.md section 11
        return

    current_spend = spend_cents / 100.0
    budget = float(team.monthly_budget_usd)

    # Update budget usage gauge
    try:
        from app.observability.metrics import TEAM_BUDGET_USAGE

        TEAM_BUDGET_USAGE.labels(team_id=str(team.id), team_name=team.name).set(
            current_spend / budget if budget > 0 else 0.0
        )
    except Exception as metric_err:
        logger.error("metrics_error_in_budget_check", error=str(metric_err))

    if current_spend >= budget:
        logger.warning(
            "budget_exceeded", team_id=team.id, current_spend=current_spend, budget=budget, request_id=request_id
        )
        # Record request rejection in Prometheus
        try:
            from app.observability.metrics import REQUEST_COUNT

            REQUEST_COUNT.labels(
                provider="none", model="none", status_code="402", team_id=str(team.id), was_fallback="False"
            ).inc()
        except Exception as metric_err:
            logger.error("metrics_error_recording_budget_rejection", error=str(metric_err))

        raise HTTPException(
            status_code=402,
            detail={
                "error": {
                    "code": "budget_exceeded",
                    "message": f"Monthly budget exceeded. Spend: ${current_spend:.2f}, Budget: ${budget:.2f}",
                    "current_spend": current_spend,
                    "budget": budget,
                    "request_id": request_id,
                }
            },
        )

    if current_spend >= 0.8 * budget:
        pct = (current_spend / budget) * 100.0
        request.state.budget_warning = f"{pct:.0f}%"


async def record_budget_spend(redis_client: redis_async.Redis, team_id: str, cost_usd: float):
    """
    Increments the monthly spend in Redis by cost_usd (converted to cents).
    """
    if cost_usd <= 0.0:
        return
    yyyymm = datetime.datetime.utcnow().strftime("%Y%m")
    budget_key = f"budget:{team_id}:month:{yyyymm}"
    cost_cents = cost_usd * 100.0

    # NOTE: The budget deduction and TPM refund are two separate Redis writes after the provider call.
    # They do not need to be atomic with each other because:
    # 1. At this scale, a crash between the two writes is rare and causes minimal/temporary inconsistency.
    # 2. Redis is used as a fast, transient counter cache; the source of truth for spend is eventually Postgres.
    # 3. Fail-open / fail-soft is acceptable for a portfolio demo.
    try:
        await redis_client.incrbyfloat(budget_key, cost_cents)
    except Exception as e:
        logger.error("redis_error_recording_budget", error=str(e))
