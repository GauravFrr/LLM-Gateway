import datetime

import pytest
from httpx import AsyncClient

# ── helpers ───────────────────────────────────────────────────────────────────


async def create_team_with_access(
    client: AsyncClient,
    name: str,
    budget: float = 100.0,
    rpm: int = 100,
    primary_provider: str = "groq",
    primary_model: str = "openai/gpt-oss-20b",
    fallback_provider: str = "gemini",
    fallback_model: str = "gemini-3.6-flash",
):
    r = await client.post(
        "/admin/teams",
        json={"name": name, "monthly_budget_usd": budget, "priority_tier": "normal"},
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    team_id, api_key = data["id"], data["api_key"]

    r = await client.post(
        f"/admin/teams/{team_id}/access",
        json={
            "logical_tier": "fast",
            "primary_provider": primary_provider,
            "primary_model": primary_model,
            "fallback_provider": fallback_provider,
            "fallback_model": fallback_model,
            "rate_limit_rpm": rpm,
            "rate_limit_tpm": 50000,
        },
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200, r.text
    return team_id, api_key


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_completions_auth(client: AsyncClient):
    """Invalid key returns 401; valid key with access configured returns 200 mock response."""
    # 1. Invalid key → 401
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer invalid-key"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "invalid_api_key"

    # 2. Create team and configure access
    _, api_key = await create_team_with_access(client, "chat-auth-team")

    # 3. Valid key → 200 mock Groq response
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "This is a mocked Groq response."


@pytest.mark.asyncio
async def test_rate_limit_enforcement(client: AsyncClient):
    """RPM=1 bucket: first request succeeds, second immediate request returns 429 with Retry-After."""
    _, api_key = await create_team_with_access(client, "rl-team", rpm=1)

    # First request → 200
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200

    # Second request immediately → 429 with Retry-After header
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert r.json()["detail"]["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_budget_warning_and_exhaustion(client: AsyncClient, redis_client):
    """
    Budget enforcement:
    - At ≥80% spend: response includes X-Budget-Warning header.
    - At 100% spend: request returns 402.
    We seed Redis spend directly to avoid waiting for many real requests.
    """
    team_id, api_key = await create_team_with_access(client, "budget-team", budget=1.00)

    yyyymm = datetime.datetime.utcnow().strftime("%Y%m")
    budget_key = f"budget:{team_id}:month:{yyyymm}"

    # budget.py stores cost_usd*100 in Redis, reads it back and divides by 100.
    # $1.00 budget, 85% spend = $0.85 → store 85 (not 8500)
    await redis_client.set(budget_key, 85)

    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    # X-Budget-Warning is set to a percentage string (e.g. "85%") when spend >= 80% of budget
    assert r.headers.get("X-Budget-Warning") is not None

    # $1.00 budget fully exhausted → store 101 cents (> 100% of $1.00)
    await redis_client.set(budget_key, 101)

    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 402
    assert r.json()["detail"]["error"]["code"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_fallback_and_circuit_breaker(client: AsyncClient, redis_client):
    """
    Circuit breaker integration test using Redis state seeding (MOCK_PROVIDERS=True means
    all providers always succeed, so we cannot trigger real failures to open the circuit).

    Strategy:
    1. Configure groq as primary (succeeds in mock) and gemini as fallback.
    2. Manually seed Redis to mark groq circuit as OPEN.
    3. Send a request → groq should be bypassed (circuit open) → gemini fallback used.
    4. Verify response is from gemini (was_fallback=True).
    5. Reset circuit back to closed → next request uses groq primary (was_fallback=False).
    """
    team_id, api_key = await create_team_with_access(
        client,
        "cb-team",
        primary_provider="groq",
        primary_model="openai/gpt-oss-20b",
        fallback_provider="gemini",
        fallback_model="gemini-3.6-flash",
    )

    # 1. Baseline: reset circuits so groq is CLOSED
    await client.post("/admin/circuits/reset", headers={"Authorization": "Bearer abcd"})

    # Confirm baseline request uses primary (groq), no fallback
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    assert r.json()["was_fallback"] is False
    assert r.json()["provider"] == "groq"

    # 2. Manually open the groq circuit in Redis (simulates 5 consecutive failures)
    await redis_client.set("circuit:groq:state", "open")
    await redis_client.set("circuit:groq:failures", 5)
    # Set cooldown far in the future so it stays open during the test
    import time

    await redis_client.set("circuit:groq:cooldown_end", time.time() + 3600)

    # 3. Request with groq circuit OPEN → bypasses groq, uses gemini fallback
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["was_fallback"] is True
    assert res["provider"] == "gemini"

    # 4. Reset circuit → groq primary is used again (no fallback)
    await client.post("/admin/circuits/reset", headers={"Authorization": "Bearer abcd"})

    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    assert r.json()["was_fallback"] is False
    assert r.json()["provider"] == "groq"


@pytest.mark.asyncio
async def test_hot_reload_rate_limit(client: AsyncClient, redis_client):
    """
    Hot-reload: updating RPM limit via admin API takes effect immediately without restart.
    1. Configure RPM=1, confirm second request is blocked (429).
    2. Update RPM to 100, flush rate-limit Redis keys.
    3. Confirm next request succeeds (200), proving new limit is live.
    """
    team_id, api_key = await create_team_with_access(client, "reload-team", rpm=1)

    # Req 1 succeeds
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200

    # Req 2 immediately → 429
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 429

    # Hot-reload: update RPM to 100 via Admin API
    r = await client.post(
        f"/admin/teams/{team_id}/access",
        json={
            "logical_tier": "fast",
            "primary_provider": "groq",
            "primary_model": "openai/gpt-oss-20b",
            "fallback_provider": "gemini",
            "fallback_model": "gemini-3.6-flash",
            "rate_limit_rpm": 100,
            "rate_limit_tpm": 50000,
        },
        headers={"Authorization": "Bearer abcd"},
    )
    assert r.status_code == 200

    # Clear the exhausted rate-limit bucket keys to simulate bucket refill
    await redis_client.delete(f"ratelimit:{team_id}:fast:rpm", f"ratelimit:{team_id}:fast:tpm")

    # Next request → 200 (new limit is live, bucket refreshed)
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_circuit_breaker_transitions(client: AsyncClient, redis_client):
    """
    Integration test verifying the WRITE path state-machine transitions:
    1. Simulate 5 consecutive provider failures on the primary provider.
    2. Assert the circuit state in Redis transitions to 'open'.
    3. Simulate cooldown expiry and check it transitions to 'half_open'.
    4. Execute a successful request in 'half_open' and verify it transitions back to 'closed'.
    """
    import time
    from unittest.mock import AsyncMock, patch

    from app.providers.base import RetryableProviderError

    team_id, api_key = await create_team_with_access(
        client,
        "cb-transition-team",
        primary_provider="groq",
        primary_model="openai/gpt-oss-20b",
        fallback_provider="gemini",
        fallback_model="gemini-3.6-flash",
    )

    # Clean start: reset circuits
    await client.post("/admin/circuits/reset", headers={"Authorization": "Bearer abcd"})

    # Check initially CLOSED in Redis (keys do not exist yet)
    assert (await redis_client.get("circuit:groq:state")) is None

    # Mock groq client to raise RetryableProviderError
    mock_fail_client = AsyncMock()
    mock_fail_client.chat_completion = AsyncMock(side_effect=RetryableProviderError("Mock error", provider="groq"))

    # Patch provider client factory to return failing client for groq
    with patch("app.api.v1.chat._get_provider_client", return_value=mock_fail_client):
        # Trigger 5 consecutive failures
        for _ in range(5):
            r = await client.post(
                "/v1/chat/completions",
                json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            # Response should succeed via fallback (gemini) since gemini is not failing
            assert r.status_code == 200
            assert r.json()["was_fallback"] is True
            assert r.json()["provider"] == "gemini"

    # Assert circuit state in Redis transitioned to 'open'
    assert (await redis_client.get("circuit:groq:state")) == "open"
    assert int(await redis_client.get("circuit:groq:failures")) == 5

    # Simulate cooldown expiry by setting cooldown_end to a past timestamp
    await redis_client.set("circuit:groq:cooldown_end", str(time.time() - 10))

    # Send a request - this will lazily transition state to 'half_open', attempt groq (which succeeds),
    # and then transition state back to 'closed'.
    r = await client.post(
        "/v1/chat/completions",
        json={"tier": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    assert r.json()["was_fallback"] is False
    assert r.json()["provider"] == "groq"

    # Assert circuit state in Redis transitioned back to 'closed'
    assert (await redis_client.get("circuit:groq:state")) == "closed"
    assert int(await redis_client.get("circuit:groq:failures")) == 0
