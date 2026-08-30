import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_authentication(client: AsyncClient):
    """Invalid admin key returns 401; valid admin key creates team successfully."""
    # Invalid admin key → 401
    r = await client.post(
        "/admin/teams",
        json={"name": "test-team"},
        headers={"Authorization": "Bearer wrong-admin-key"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body["detail"]["error"]["code"] == "invalid_api_key"

    # Valid admin key → team created successfully
    r = await client.post(
        "/admin/teams",
        json={"name": "test-team", "monthly_budget_usd": 100.0, "priority_tier": "normal"},
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "test-team"
    assert "id" in data
    assert "api_key" in data


@pytest.mark.asyncio
async def test_configure_model_access(client: AsyncClient):
    """Admin can configure model access tiers for a team."""
    r = await client.post(
        "/admin/teams",
        json={"name": "team-access", "monthly_budget_usd": 500.0, "priority_tier": "high"},
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200
    team_id = r.json()["id"]

    payload = {
        "logical_tier": "fast",
        "primary_provider": "groq",
        "primary_model": "openai/gpt-oss-20b",
        "fallback_provider": "gemini",
        "fallback_model": "gemini-3.6-flash",
        "rate_limit_rpm": 1000,
        "rate_limit_tpm": 500000,
    }
    r = await client.post(
        f"/admin/teams/{team_id}/access",
        json=payload,
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200
    access = r.json()
    assert access["logical_tier"] == "fast"
    assert access["primary_provider"] == "groq"
    assert access["fallback_provider"] == "gemini"


@pytest.mark.asyncio
async def test_circuit_reset_endpoint(client: AsyncClient):
    """Admin circuit reset endpoint returns 200 with success message."""
    r = await client.post(
        "/admin/circuits/reset",
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200
    assert r.json()["message"] == "All circuit states reset to CLOSED."
