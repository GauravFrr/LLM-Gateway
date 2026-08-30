from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.budget import check_budget
from app.models.db import Team


@pytest.mark.asyncio
async def test_budget_check_allowed():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"
    # Reset state to default
    delattr(request.state, "budget_warning") if hasattr(request.state, "budget_warning") else None

    team = Team(id="team-id", name="test-team", monthly_budget_usd=10.0)

    # Mock Redis returning 500 cents ($5.00) spend
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="500.0")

    await check_budget(request, team, mock_redis)
    assert not hasattr(request.state, "budget_warning") or request.state.budget_warning is None


@pytest.mark.asyncio
async def test_budget_check_warning():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"
    delattr(request.state, "budget_warning") if hasattr(request.state, "budget_warning") else None

    team = Team(id="team-id", name="test-team", monthly_budget_usd=10.0)

    # Mock Redis returning 850 cents ($8.50) spend (85% of budget)
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="850.0")

    await check_budget(request, team, mock_redis)
    assert request.state.budget_warning == "85%"


@pytest.mark.asyncio
async def test_budget_check_blocked():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"

    team = Team(id="team-id", name="test-team", monthly_budget_usd=10.0)

    # Mock Redis returning 1100 cents ($11.00) spend
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="1100.0")

    with pytest.raises(HTTPException) as excinfo:
        await check_budget(request, team, mock_redis)

    assert excinfo.value.status_code == 402
    assert excinfo.value.detail["error"]["code"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_budget_check_fail_open():
    request = MagicMock()
    request.state = MagicMock()
    request.state.request_id = "test-req"

    team = Team(id="team-id", name="test-team", monthly_budget_usd=10.0)

    # Mock Redis raising exception on read
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=Exception("Redis failure"))

    # Should fail open without raising exception
    await check_budget(request, team, mock_redis)
