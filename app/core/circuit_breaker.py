import time
from datetime import UTC, datetime

import redis.asyncio as redis_async
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import ProviderHealthEvent

logger = structlog.get_logger()


class RedisCircuitBreaker:
    """
    Distributed Circuit Breaker using Redis for state/consecutive failures
    and PostgreSQL for state transition history logging.
    """

    def __init__(
        self, redis_client: redis_async.Redis, provider: str, failure_threshold: int = 5, cooldown_seconds: int = 30
    ):
        self.redis = redis_client
        self.provider = provider.lower()
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

        self.state_key = f"circuit:{self.provider}:state"
        self.failures_key = f"circuit:{self.provider}:failures"
        self.cooldown_end_key = f"circuit:{self.provider}:cooldown_end"

    async def get_state(self, db_session: AsyncSession) -> str:
        """
        Retrieves the current circuit state. Lazily transitions from OPEN
        to HALF_OPEN if the cooldown period has expired.
        """
        try:
            state = await self.redis.get(self.state_key) or "closed"
        except Exception as e:
            logger.error("redis_error_reading_circuit_state", provider=self.provider, error=str(e))
            return "closed"  # Fail-open behavior on Redis failure

        if state == "open":
            try:
                cooldown_end_str = await self.redis.get(self.cooldown_end_key)
                if cooldown_end_str:
                    cooldown_end = float(cooldown_end_str)
                    if time.time() >= cooldown_end:
                        # Cooldown expired: transition to half_open
                        await self.transition_to("half_open", "Cooldown period expired", db_session)
                        return "half_open"
            except Exception as e:
                logger.error("redis_error_checking_cooldown", provider=self.provider, error=str(e))

        return state

    async def record_success(self, db_session: AsyncSession):
        """
        Called upon a successful request. Resets failure counters.
        If current state was open or half_open, transitions back to closed.
        """
        try:
            state = await self.redis.get(self.state_key) or "closed"
            # Always reset consecutive failures to 0 on success to prevent old failure accumulations
            await self.redis.set(self.failures_key, 0)
        except Exception as e:
            logger.error("redis_error_recording_circuit_success", provider=self.provider, error=str(e))
            state = "closed"

        if state in ("open", "half_open"):
            await self.transition_to("closed", "Successful request in half_open state", db_session)

    async def record_failure(self, reason: str, db_session: AsyncSession):
        """
        Called upon a failed request. Increments failure counters.
        If failures >= threshold (or if currently in half_open), opens the circuit.
        """
        try:
            state = await self.redis.get(self.state_key) or "closed"
        except Exception as e:
            logger.error("redis_error_checking_state_on_failure", provider=self.provider, error=str(e))
            state = "closed"

        if state == "closed":
            try:
                failures = await self.redis.incr(self.failures_key)
                if failures >= self.failure_threshold:
                    # Trip the circuit
                    cooldown_end = time.time() + self.cooldown_seconds
                    await self.redis.set(self.cooldown_end_key, cooldown_end)
                    await self.transition_to(
                        "open",
                        f"Consecutive failures exceeded threshold ({failures}/{self.failure_threshold}): {reason}",
                        db_session,
                    )
            except Exception as e:
                logger.error("redis_error_updating_failures", provider=self.provider, error=str(e))
        elif state == "half_open":
            # Any failure in HALF_OPEN trips the circuit breaker back to OPEN and resets cooldown
            try:
                cooldown_end = time.time() + self.cooldown_seconds
                await self.redis.set(self.cooldown_end_key, cooldown_end)
                await self.transition_to("open", f"Test request failed in half_open state: {reason}", db_session)
            except Exception as e:
                logger.error("redis_error_updating_cooldown_on_half_open", provider=self.provider, error=str(e))

    async def transition_to(self, new_state: str, reason: str, db_session: AsyncSession):
        """
        Updates the circuit state in Redis and logs a transition event to PostgreSQL.
        """
        logger.info(
            "circuit_state_transition",
            provider=self.provider,
            old_state=await self.redis.get(self.state_key) or "closed",
            new_state=new_state,
            reason=reason,
        )

        try:
            await self.redis.set(self.state_key, new_state)

            # Update Prometheus circuit state gauge
            try:
                from app.observability.metrics import CIRCUIT_STATE

                val = 0 if new_state == "closed" else (1 if new_state == "half_open" else 2)
                CIRCUIT_STATE.labels(provider=self.provider).set(val)
            except Exception as metric_err:
                logger.error("metrics_error_in_circuit_transition", error=str(metric_err))
        except Exception as e:
            logger.error("redis_error_updating_state", provider=self.provider, error=str(e))

        # Log event to database
        try:
            event = ProviderHealthEvent(
                provider=self.provider, event_type=f"circuit_{new_state}", reason=reason, created_at=datetime.now(UTC)
            )
            db_session.add(event)
            await db_session.commit()
        except Exception as e:
            logger.error("postgres_error_saving_circuit_event", provider=self.provider, error=str(e))
            # Session rollback on failure to prevent transaction contamination
            await db_session.rollback()
