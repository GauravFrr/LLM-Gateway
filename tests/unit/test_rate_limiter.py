import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app.core.rate_limiter import check_rate_limit
from app.models.db import Team, TeamModelAccess
from app.models.schemas import ChatCompletionRequest, Message

@pytest.mark.asyncio
async def test_rate_limiter_allowed():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"

    body = ChatCompletionRequest(
        tier="balanced",
        messages=[Message(role="user", content="hello")]
    )

    access = TeamModelAccess(
        logical_tier="balanced",
        rate_limit_rpm=10,
        rate_limit_tpm=1000,
        primary_provider="gemini",
        primary_model="gemini-3.6-flash"
    )
    team = Team(id="team-id", name="test-team", model_accesses=[access])

    mock_redis = AsyncMock()
    mock_script = AsyncMock(return_value=[1, 0])
    
    with patch("app.core.rate_limiter.rate_limiter.get_script", return_value=mock_script):
        await check_rate_limit(request, body, team, mock_redis)
        
    mock_script.assert_called_once()

@pytest.mark.asyncio
async def test_rate_limiter_blocked():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"

    body = ChatCompletionRequest(
        tier="balanced",
        messages=[Message(role="user", content="hello")]
    )

    access = TeamModelAccess(
        logical_tier="balanced",
        rate_limit_rpm=10,
        rate_limit_tpm=1000,
        primary_provider="gemini",
        primary_model="gemini-3.6-flash"
    )
    team = Team(id="team-id", name="test-team", model_accesses=[access])

    mock_redis = AsyncMock()
    mock_script = AsyncMock(return_value=[0, 5])
    
    with patch("app.core.rate_limiter.rate_limiter.get_script", return_value=mock_script):
        with pytest.raises(HTTPException) as excinfo:
            await check_rate_limit(request, body, team, mock_redis)
            
        assert excinfo.value.status_code == 429
        assert excinfo.value.headers["Retry-After"] == "5"

@pytest.mark.asyncio
async def test_rate_limiter_fail_open():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"

    body = ChatCompletionRequest(
        tier="balanced",
        messages=[Message(role="user", content="hello")]
    )

    access = TeamModelAccess(
        logical_tier="balanced",
        rate_limit_rpm=10,
        rate_limit_tpm=1000,
        primary_provider="gemini",
        primary_model="gemini-3.6-flash"
    )
    team = Team(id="team-id", name="test-team", model_accesses=[access])

    mock_redis = AsyncMock()
    mock_script = AsyncMock(side_effect=Exception("Redis offline"))
    
    with patch("app.core.rate_limiter.rate_limiter.get_script", return_value=mock_script):
        # Should fail open without raising exception
        await check_rate_limit(request, body, team, mock_redis)
