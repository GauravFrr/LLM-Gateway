import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.chat import chat_completions
from app.models.db import Team, TeamModelAccess
from app.models.schemas import ChatCompletionRequest, Message
from app.providers.base import RetryableProviderError


@pytest.fixture
def mock_team_balanced():
    access = TeamModelAccess(
        logical_tier="balanced",
        primary_provider="groq",
        primary_model="openai/gpt-oss-20b",
        fallback_provider="gemini",
        fallback_model="gemini-3.6-flash",
        rate_limit_rpm=100,
        rate_limit_tpm=50000,
    )
    return Team(id="team-123", name="test-team", model_accesses=[access])


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_fallback_success_on_primary_fail(mock_team_balanced, mock_db):
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "req-123"
    request.state.budget_warning = None
    response = MagicMock()

    body = ChatCompletionRequest(tier="balanced", messages=[Message(role="user", content="hello")])

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    # Primary fails with Retryable Error, Fallback succeeds
    mock_primary_client = AsyncMock()
    mock_primary_client.chat_completion = AsyncMock(side_effect=RetryableProviderError("Timeout", provider="groq"))

    mock_fallback_client = AsyncMock()
    mock_fallback_client.chat_completion = AsyncMock(return_value=("Fallback response", 10, 20))

    def side_effect(provider_name, request_id):
        if provider_name == "groq":
            return mock_primary_client
        return mock_fallback_client

    with patch("app.api.v1.chat._get_provider_client", side_effect=side_effect):
        res = await chat_completions(
            request=request,
            body=body,
            response=response,
            team=mock_team_balanced,
            _rate_limit=None,
            _budget=None,
            redis_client=mock_redis,
            db=mock_db,
        )

        assert res.was_fallback is True
        assert res.provider == "gemini"
        assert res.content == "Fallback response"


@pytest.mark.asyncio
async def test_fallback_also_fails_throws_503(mock_team_balanced, mock_db):
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "req-123"
    request.state.budget_warning = None
    response = MagicMock()

    body = ChatCompletionRequest(tier="balanced", messages=[Message(role="user", content="hello")])

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    # Both fail
    mock_primary_client = AsyncMock()
    mock_primary_client.chat_completion = AsyncMock(side_effect=RetryableProviderError("Timeout", provider="groq"))

    mock_fallback_client = AsyncMock()
    mock_fallback_client.chat_completion = AsyncMock(
        side_effect=RetryableProviderError("Fallback Timeout", provider="gemini")
    )

    def side_effect(provider_name, request_id):
        if provider_name == "groq":
            return mock_primary_client
        return mock_fallback_client

    with patch("app.api.v1.chat._get_provider_client", side_effect=side_effect):
        with pytest.raises(HTTPException) as excinfo:
            await chat_completions(
                request=request,
                body=body,
                response=response,
                team=mock_team_balanced,
                _rate_limit=None,
                _budget=None,
                redis_client=mock_redis,
                db=mock_db,
            )

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["error"]["code"] == "provider_unavailable"
        assert "groq" in excinfo.value.detail["error"]["providers_attempted"]
        assert "gemini" in excinfo.value.detail["error"]["providers_attempted"]


@pytest.mark.asyncio
async def test_circuit_trips_to_open_after_failures(mock_db):
    from app.core.circuit_breaker import RedisCircuitBreaker

    mock_redis = AsyncMock()
    redis_state = {}

    async def mock_get(key):
        return redis_state.get(key)

    async def mock_set(key, val):
        redis_state[key] = str(val)
        return True

    async def mock_incr(key):
        val = int(redis_state.get(key, 0)) + 1
        redis_state[key] = str(val)
        return val

    mock_redis.get = mock_get
    mock_redis.set = mock_set
    mock_redis.incr = mock_incr

    cb = RedisCircuitBreaker(mock_redis, "groq", failure_threshold=5, cooldown_seconds=30)

    # 1. State starts CLOSED
    state = await cb.get_state(mock_db)
    assert state == "closed"

    # 2. Record 5 failures
    for i in range(5):
        await cb.record_failure("error", mock_db)

    # 3. State transitions to OPEN
    state = await cb.get_state(mock_db)
    assert state == "open"


@pytest.mark.asyncio
async def test_circuit_half_open_cooldown_expiry(mock_db):
    from app.core.circuit_breaker import RedisCircuitBreaker

    mock_redis = AsyncMock()
    redis_state = {
        "circuit:groq:state": "open",
        "circuit:groq:cooldown_end": str(time.time() - 10),  # Cooldown expired
    }

    async def mock_get(key):
        return redis_state.get(key)

    async def mock_set(key, val):
        redis_state[key] = str(val)
        return True

    mock_redis.get = mock_get
    mock_redis.set = mock_set

    cb = RedisCircuitBreaker(mock_redis, "groq", cooldown_seconds=30)

    # Lazy state check triggers HALF_OPEN
    state = await cb.get_state(mock_db)
    assert state == "half_open"
    assert redis_state["circuit:groq:state"] == "half_open"


@pytest.mark.asyncio
async def test_circuit_half_open_success_transitions_closed(mock_db):
    from app.core.circuit_breaker import RedisCircuitBreaker

    mock_redis = AsyncMock()
    redis_state = {"circuit:groq:state": "half_open"}

    async def mock_get(key):
        return redis_state.get(key)

    async def mock_set(key, val):
        redis_state[key] = str(val)
        return True

    mock_redis.get = mock_get
    mock_redis.set = mock_set

    cb = RedisCircuitBreaker(mock_redis, "groq")

    await cb.record_success(mock_db)
    assert redis_state["circuit:groq:state"] == "closed"
