import hashlib
import structlog
from fastapi import Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import redis.asyncio as redis_async

from app.db.session import get_db, get_redis
from app.models.db import Team

logger = structlog.get_logger()
security = HTTPBearer(auto_error=False)

async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis_client: redis_async.Redis = Depends(get_redis)
) -> Team:
    """
    Validates the bearer token against cached Redis hashes and Postgres.
    Returns the validated Team model on success, raises 401 otherwise.
    """
    request_id = getattr(request.state, "request_id", "req_unknown")

    if not credentials or not credentials.credentials:
        logger.warning("auth_failed", reason="missing_token", request_id=request_id)
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "invalid_api_key",
                    "message": "Authorization header is missing or malformed.",
                    "request_id": request_id
                }
            }
        )

    token = credentials.credentials
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache_key = f"apikey:{token_hash}"

    # 1. Check Redis cache
    try:
        team_id = await redis_client.get(cache_key)
    except Exception as e:
        logger.error("redis_error_in_auth", error=str(e), request_id=request_id)
        team_id = None  # Fail open for cache lookup, fallback to DB

    if team_id:
        # Cache hit: Load from DB by team_id to get fresh budget config
        result = await db.execute(select(Team).where(Team.id == team_id, Team.is_active == True))
        team = result.scalar_one_or_none()
        if team:
            return team

    # 2. Cache miss: Check Postgres directly by key hash
    result = await db.execute(
        select(Team).where(Team.api_key_hash == token_hash, Team.is_active == True)
    )
    team = result.scalar_one_or_none()

    if not team:
        logger.warning("auth_failed", reason="invalid_token", request_id=request_id)
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "invalid_api_key",
                    "message": "The provided API key is invalid or inactive.",
                    "request_id": request_id
                }
            }
        )

    # 3. Write back to cache
    try:
        # Cache key hash -> team_id with 5 min TTL (300 seconds)
        await redis_client.setex(cache_key, 300, str(team.id))
    except Exception as e:
        logger.error("redis_write_error_in_auth", error=str(e), request_id=request_id)

    return team
