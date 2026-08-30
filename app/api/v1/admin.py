import hashlib
import secrets
import uuid
from datetime import UTC

import structlog
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.db.session import get_db, get_redis
from app.models.db import ProviderHealthEvent, Team, TeamModelAccess
from app.models.schemas import (
    ModelAccessRequest,
    ModelAccessResponse,
    TeamCreateRequest,
    TeamCreateResponse,
)

router = APIRouter()
logger = structlog.get_logger()
security = HTTPBearer()


def require_admin(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Checks the bearer token against settings.ADMIN_API_KEY.
    """
    if not credentials or credentials.credentials != settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "invalid_api_key",
                    "message": "Invalid or missing admin API key.",
                    "request_id": "req_admin",
                }
            },
        )
    return True


@router.post("/teams", response_model=TeamCreateResponse, dependencies=[Depends(require_admin)])
async def create_team(body: TeamCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    Creates a new team and returns its plaintext API key once.
    """
    result = await db.execute(select(Team).where(Team.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "invalid_request", "message": f"Team name '{body.name}' already exists."}},
        )

    plaintext_key = f"key_{secrets.token_urlsafe(32)}"
    api_key_hash = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()

    team = Team(
        name=body.name,
        api_key_hash=api_key_hash,
        monthly_budget_usd=body.monthly_budget_usd,
        priority_tier=body.priority_tier,
    )

    db.add(team)
    await db.commit()
    await db.refresh(team)

    logger.info("team_created", team_name=team.name, team_id=str(team.id))

    return TeamCreateResponse(
        id=team.id,
        name=team.name,
        monthly_budget_usd=team.monthly_budget_usd,
        priority_tier=team.priority_tier,
        is_active=team.is_active,
        api_key=plaintext_key,
    )


@router.post("/teams/{id}/access", response_model=ModelAccessResponse, dependencies=[Depends(require_admin)])
async def create_or_update_model_access(id: uuid.UUID, body: ModelAccessRequest, db: AsyncSession = Depends(get_db)):
    """
    Adds or updates logical tier model access mapping and limits for a team.
    """
    result = await db.execute(select(Team).where(Team.id == id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "invalid_request", "message": f"Team with ID '{id}' not found."}}
        )

    # Upsert TeamModelAccess
    result = await db.execute(
        select(TeamModelAccess).where(TeamModelAccess.team_id == id, TeamModelAccess.logical_tier == body.logical_tier)
    )
    access = result.scalar_one_or_none()

    if not access:
        access = TeamModelAccess(
            team_id=id,
            logical_tier=body.logical_tier,
            primary_provider=body.primary_provider,
            primary_model=body.primary_model,
            fallback_provider=body.fallback_provider,
            fallback_model=body.fallback_model,
            rate_limit_rpm=body.rate_limit_rpm,
            rate_limit_tpm=body.rate_limit_tpm,
        )
        db.add(access)
    else:
        access.primary_provider = body.primary_provider
        access.primary_model = body.primary_model
        access.fallback_provider = body.fallback_provider
        access.fallback_model = body.fallback_model
        access.rate_limit_rpm = body.rate_limit_rpm
        access.rate_limit_tpm = body.rate_limit_tpm

    await db.commit()
    await db.refresh(access)

    logger.info("model_access_configured", team_id=str(id), tier=body.logical_tier)

    return access


@router.post("/circuits/reset", dependencies=[Depends(require_admin)])
async def reset_circuits(db: AsyncSession = Depends(get_db), redis_client=Depends(get_redis)):
    """
    Resets circuit breaker states for all providers in Redis, updates Prometheus gauges in-memory,
    and logs the reset event to the database.
    """
    from datetime import datetime

    from app.observability.metrics import CIRCUIT_STATE

    providers = ["groq", "gemini", "claude", "ollama"]

    for provider in providers:
        state_key = f"circuit:{provider}:state"
        failures_key = f"circuit:{provider}:failures"
        cooldown_end_key = f"circuit:{provider}:cooldown_end"

        # Clear keys in Redis
        try:
            await redis_client.delete(state_key, failures_key, cooldown_end_key)
        except Exception as e:
            logger.error("redis_error_during_reset", provider=provider, error=str(e))

        # Update Prometheus gauge in memory
        try:
            CIRCUIT_STATE.labels(provider=provider).set(0)
        except Exception as e:
            logger.error("metrics_error_during_reset", provider=provider, error=str(e))

        # Log to PostgreSQL
        try:
            event = ProviderHealthEvent(
                provider=provider,
                event_type="circuit_closed",
                reason="Manual admin reset request",
                created_at=datetime.now(UTC),
            )
            db.add(event)
        except Exception as e:
            logger.error("postgres_error_during_reset", provider=provider, error=str(e))

    try:
        await db.commit()
    except Exception as e:
        logger.error("db_commit_error_during_reset", error=str(e))
        await db.rollback()

    return {"status": "ok", "message": "All circuit states reset to CLOSED."}
