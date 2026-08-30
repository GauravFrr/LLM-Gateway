import os
import time

import redis.asyncio as redis_async
import structlog
from fastapi import Depends, HTTPException, Request

from app.core.auth import require_auth
from app.db.session import get_redis
from app.models.db import Team
from app.models.schemas import ChatCompletionRequest

logger = structlog.get_logger()

# Load the Lua script for token bucket check-and-decrement
LUA_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(LUA_DIR, "lua", "token_bucket.lua")) as f:
    TOKEN_BUCKET_LUA = f.read()

# Define Lua adjustment script to refund unused TPM tokens
REFUND_LUA = """
local tpm_key = KEYS[1]
local refund = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])

local data = redis.call('HMGET', tpm_key, 'tokens', 'last_updated')
local tokens = tonumber(data[1])
if tokens then
    tokens = math.min(limit, tokens + refund)
    redis.call('HSET', tpm_key, 'tokens', tokens)
end
return 1
"""


class RateLimiter:
    """
    Handles script registration and script execution caching.
    """

    def __init__(self):
        self._script = None
        self._refund_script = None

    def get_script(self, redis_client):
        if not self._script:
            self._script = redis_client.register_script(TOKEN_BUCKET_LUA)
        return self._script

    def get_refund_script(self, redis_client):
        if not self._refund_script:
            self._refund_script = redis_client.register_script(REFUND_LUA)
        return self._refund_script


rate_limiter = RateLimiter()


async def check_rate_limit(
    request: Request,
    body: ChatCompletionRequest,
    team: Team = Depends(require_auth),
    redis_client: redis_async.Redis = Depends(get_redis),
):
    """
    Enforces RPM and TPM limits via the atomic token_bucket.lua script in Redis.
    """
    request_id = getattr(request.state, "request_id", "req_unknown")
    tier = body.tier

    # Find the team's access config for this tier
    access = next((a for a in team.model_accesses if a.logical_tier == tier), None)
    if not access:
        logger.warning(
            "rate_limit_check_failed", reason="tier_not_allowed", tier=tier, team_id=team.id, request_id=request_id
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "invalid_request",
                    "message": f"Team does not have access to the '{tier}' tier.",
                    "request_id": request_id,
                }
            },
        )

    rpm_limit = access.rate_limit_rpm
    tpm_limit = access.rate_limit_tpm

    # Estimate TPM using max_tokens or default to 500
    tpm_estimate = body.max_tokens or 500

    rpm_key = f"ratelimit:{team.id}:{tier}:rpm"
    tpm_key = f"ratelimit:{team.id}:{tier}:tpm"

    now = time.time()

    try:
        script = rate_limiter.get_script(redis_client)
        res = await script(keys=[rpm_key, tpm_key], args=[now, rpm_limit, tpm_limit, tpm_estimate])
        allowed = res[0]
        retry_after = res[1]
        rejection_type = res[2] if len(res) > 2 else "rpm"
    except Exception as e:
        logger.error("redis_error_in_rate_limiter", error=str(e), request_id=request_id)
        # Fail-open design on Redis failure as per 08_SECURITY.md section 11
        return

    if not allowed:
        logger.warning(
            "rate_limit_exceeded", team_id=team.id, tier=tier, retry_after=retry_after, request_id=request_id
        )
        # Record Prometheus metrics immediately before raising exception
        try:
            from app.observability.metrics import RATE_LIMIT_REJECTION, REQUEST_COUNT

            REQUEST_COUNT.labels(
                provider="none", model="none", status_code="429", team_id=str(team.id), was_fallback="False"
            ).inc()
            RATE_LIMIT_REJECTION.labels(
                team_id=str(team.id), team_name=team.name, logical_tier=tier, rejection_type=rejection_type
            ).inc()
        except Exception as metric_err:
            logger.error("metrics_error_in_rate_limiter", error=str(metric_err))

        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            detail={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded. Please retry after {retry_after} seconds.",
                    "retry_after_seconds": retry_after,
                    "request_id": request_id,
                }
            },
        )


async def refund_tpm_tokens(redis_client: redis_async.Redis, team_id: str, tier: str, limit: int, refund_amount: int):
    """
    Refunds unused tokens back to the TPM bucket (called post-request).
    """
    if refund_amount == 0:
        return
    tpm_key = f"ratelimit:{team_id}:{tier}:tpm"
    try:
        script = rate_limiter.get_refund_script(redis_client)
        await script(keys=[tpm_key], args=[refund_amount, limit])
    except Exception as e:
        logger.error("redis_error_in_tpm_refund", error=str(e))
