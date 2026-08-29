import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.main import app
from app.core.rate_limiter import check_rate_limit
from app.core.budget import check_budget
from app.models.db import Team, TeamModelAccess
from app.models.schemas import ChatCompletionRequest, Message

client = TestClient(app)

def test_metrics_endpoint_unauthenticated():
    """
    Assert that /metrics is accessible without authentication and returns Prometheus metrics.
    """
    with TestClient(app) as tc:
        response = tc.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "llm_gateway_circuit_breaker_state" in response.text


@pytest.mark.asyncio
async def test_rate_limit_rejection_metrics():
    """
    Verify that check_rate_limit increments rejections and request counts on block.
    """
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req-rate-limit"

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
    team = Team(id="team-rate-limit-test", name="test-team", model_accesses=[access])

    mock_redis = AsyncMock()
    # Mock Lua script returning blocked with 5s retry and rejection type "rpm"
    mock_script = AsyncMock(return_value=[0, 5, "rpm"])
    
    # Reset count in Prometheus client registry if needed, or just assert it raises and increments
    from app.observability.metrics import RATE_LIMIT_REJECTION, REQUEST_COUNT
    
    # Get current value
    try:
        before_count = REQUEST_COUNT.labels(
            provider="none",
            model="none",
            status_code="429",
            team_id=str(team.id),
            was_fallback="False"
        )._value.get()
    except Exception:
        before_count = 0

    with patch("app.core.rate_limiter.rate_limiter.get_script", return_value=mock_script):
        with pytest.raises(HTTPException) as excinfo:
            await check_rate_limit(request, body, team, mock_redis)
            
        assert excinfo.value.status_code == 429
        
    after_count = REQUEST_COUNT.labels(
        provider="none",
        model="none",
        status_code="429",
        team_id=str(team.id),
        was_fallback="False"
    )._value.get()
    
    assert after_count == before_count + 1

@pytest.mark.asyncio
async def test_budget_rejection_metrics():
    """
    Verify that check_budget increments request count on block.
    """
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req-budget"

    team = Team(id="team-budget-test", name="test-team", monthly_budget_usd=10.0)

    mock_redis = AsyncMock()
    # Mock Redis returning spend of $11.00 (exceeded)
    mock_redis.get = AsyncMock(return_value="1100.0")
    
    from app.observability.metrics import REQUEST_COUNT
    
    try:
        before_count = REQUEST_COUNT.labels(
            provider="none",
            model="none",
            status_code="402",
            team_id=str(team.id),
            was_fallback="False"
        )._value.get()
    except Exception:
        before_count = 0

    with pytest.raises(HTTPException) as excinfo:
        await check_budget(request, team, mock_redis)
        
    assert excinfo.value.status_code == 402
        
    after_count = REQUEST_COUNT.labels(
        provider="none",
        model="none",
        status_code="402",
        team_id=str(team.id),
        was_fallback="False"
    )._value.get()
    
    assert after_count == before_count + 1
