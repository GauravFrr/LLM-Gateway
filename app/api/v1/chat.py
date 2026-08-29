import time
import structlog
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.models.schemas import ChatCompletionRequest, ChatCompletionResponse, Usage
from app.config import settings
from app.db.session import get_redis, get_db
from app.models.db import Team
from app.core.auth import require_auth
from app.core.rate_limiter import check_rate_limit, refund_tpm_tokens
from app.core.budget import check_budget, record_budget_spend
from app.core.circuit_breaker import RedisCircuitBreaker
from app.core.router import router as router_layer
from app.providers import (
    GeminiProvider,
    ClaudeProvider,
    GroqProvider,
    OllamaProvider,
    GatewayProviderError,
    RetryableProviderError
)

router = APIRouter()
logger = structlog.get_logger()

# Pricing configuration for cost computation (USD per 1,000 tokens)
PRICING = {
    "gemini": {
        "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
        "gemini-2.0-flash": {"input": 0.000075, "output": 0.0003},
        "gemini-2.5-flash": {"input": 0.000075, "output": 0.0003},
        "gemini-3.5-flash": {"input": 0.000075, "output": 0.0003},
        "gemini-3.6-flash": {"input": 0.000075, "output": 0.0003},
    },
    "claude": {
        "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
        "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
    },
    "groq": {
        "llama-3.1-8b-instant": {"input": 0.00005, "output": 0.00005},
        "openai/gpt-oss-20b": {"input": 0.00005, "output": 0.00005},
        "llama3-8b-8192": {"input": 0.00005, "output": 0.00008},
    },
    "ollama": {}
}


def compute_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate the token cost for a model call using a static pricing registry.
    """
    if input_tokens is None:
        input_tokens = 0
    if output_tokens is None:
        output_tokens = 0
        
    provider_pricing = PRICING.get(provider.lower(), {})
    model_pricing = provider_pricing.get(model.lower())
    
    if not model_pricing:
        for key, value in provider_pricing.items():
            if model.lower().startswith(key):
                model_pricing = value
                break
                
    if not model_pricing:
        return 0.0
        
    input_cost = (input_tokens / 1000.0) * model_pricing.get("input", 0.0)
    output_cost = (output_tokens / 1000.0) * model_pricing.get("output", 0.0)
    return round(input_cost + output_cost, 8)

def _get_provider_client(provider_name: str, request_id: str):
    """
    Instantiates the appropriate provider client wrapper.
    """
    provider_lower = provider_name.lower()
    if provider_lower == "gemini":
        return GeminiProvider(api_key=settings.GEMINI_API_KEY)
    elif provider_lower == "claude":
        return ClaudeProvider(api_key=settings.ANTHROPIC_API_KEY)
    elif provider_lower == "groq":
        return GroqProvider(api_key=settings.GROQ_API_KEY)
    elif provider_lower == "ollama":
        return OllamaProvider(base_url=settings.OLLAMA_BASE_URL)
    else:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "internal_error",
                    "message": f"Unsupported provider '{provider_name}' resolved.",
                    "request_id": request_id
                }
            }
        )

@router.post("/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    response: Response,
    team: Team = Depends(require_auth),
    _rate_limit=Depends(check_rate_limit),
    _budget=Depends(check_budget),
    redis_client=Depends(get_redis),
    db=Depends(get_db)
):
    """
    Unified chat completion endpoint acting as an LLM proxy.
    Routes requests through authentication, monthly budget, rate limits, and fallback resilient router.
    """
    request_id = getattr(request.state, "request_id", "req_unknown")
    tier = body.tier

    # 1. Resolve logical tier mapping from DB
    access = router_layer.resolve_route(team, tier, request_id)

    primary_provider = access.primary_provider
    primary_model = access.primary_model
    fallback_provider = access.fallback_provider
    fallback_model = access.fallback_model

    tpm_estimate = body.max_tokens or 500

    # Initialize circuit breakers
    primary_cb = RedisCircuitBreaker(redis_client, primary_provider)
    fallback_cb = RedisCircuitBreaker(redis_client, fallback_provider) if fallback_provider else None

    # Track execution details
    executed_provider = None
    executed_model = None
    was_fallback = False
    content = None
    input_tokens = 0
    output_tokens = 0
    provider_duration = 0.0

    start_time = time.perf_counter()

    def record_completed_metrics(status_code: str, prov: str, model_name: str, fallback_used: bool, cost_usd: float = 0.0):
        total_time = time.perf_counter() - start_time
        overhead = total_time - provider_duration
        
        try:
            from app.observability.metrics import (
                REQUEST_LATENCY, REQUEST_COUNT, GATEWAY_OVERHEAD,
                TEAM_SPEND, TOKEN_COUNT, FALLBACK_COUNT
            )
            REQUEST_LATENCY.labels(
                provider=prov or "none",
                model=model_name or "none",
                status_code=str(status_code),
                team_id=str(team.id),
                was_fallback=str(fallback_used)
            ).observe(total_time)
            
            REQUEST_COUNT.labels(
                provider=prov or "none",
                model=model_name or "none",
                status_code=str(status_code),
                team_id=str(team.id),
                was_fallback=str(fallback_used)
            ).inc()
            
            GATEWAY_OVERHEAD.labels(
                team_id=str(team.id),
                logical_tier=tier
            ).observe(overhead)
            
            if status_code == "200" and prov:
                if cost_usd > 0.0:
                    TEAM_SPEND.labels(team_id=str(team.id), team_name=team.name, provider=prov).inc(cost_usd)
                if input_tokens > 0:
                    TOKEN_COUNT.labels(team_id=str(team.id), provider=prov, token_type="input").inc(input_tokens)
                if output_tokens > 0:
                    TOKEN_COUNT.labels(team_id=str(team.id), provider=prov, token_type="output").inc(output_tokens)
            
            if fallback_used and status_code == "200" and fallback_provider:
                FALLBACK_COUNT.labels(
                    team_id=str(team.id),
                    logical_tier=tier,
                    fallback_provider=fallback_provider
                ).inc()
                
        except Exception as metric_err:
            logger.error("metrics_error_in_completed_request", error=str(metric_err))

    # 2. Check primary circuit state
    primary_state = await primary_cb.get_state(db)
    attempt_primary = primary_state in ("closed", "half_open")

    if attempt_primary:
        logger.info(
            "routing_primary_attempt",
            request_id=request_id,
            provider=primary_provider,
            model=primary_model,
            circuit_state=primary_state
        )
        prov_start = time.perf_counter()
        try:
            # tenacity retry config: retry exactly once (2 attempts total) with exponential backoff
            retryer = AsyncRetrying(
                stop=stop_after_attempt(2),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
                retry=retry_if_exception_type(RetryableProviderError),
                reraise=True
            )
            
            async for state in retryer:
                with state:
                    client = _get_provider_client(primary_provider, request_id)
                    content, input_tokens, output_tokens = await client.chat_completion(
                        model=primary_model,
                        messages=[msg.model_dump() for msg in body.messages],
                        max_tokens=body.max_tokens,
                    )
            
            # Primary Call Success!
            provider_duration += time.perf_counter() - prov_start
            executed_provider = primary_provider
            executed_model = primary_model
            await primary_cb.record_success(db)
            
        except Exception as e:
            logger.warning(
                "primary_provider_failed",
                request_id=request_id,
                provider=primary_provider,
                error=str(e)
            )
            # Record failure against primary circuit breaker if it's not a rate limit
            if getattr(e, "trips_circuit", True):
                await primary_cb.record_failure(str(e), db)
            
            # Record failed primary attempt in metrics
            prov_code = "502"
            from app.providers.base import ProviderRateLimitError
            if isinstance(e, ProviderRateLimitError):
                prov_code = "429"
            
            primary_duration = time.perf_counter() - prov_start
            provider_duration += primary_duration
            
            try:
                from app.observability.metrics import REQUEST_LATENCY, REQUEST_COUNT
                REQUEST_LATENCY.labels(
                    provider=primary_provider,
                    model=primary_model,
                    status_code=prov_code,
                    team_id=str(team.id),
                    was_fallback="False"
                ).observe(primary_duration)
                
                REQUEST_COUNT.labels(
                    provider=primary_provider,
                    model=primary_model,
                    status_code=prov_code,
                    team_id=str(team.id),
                    was_fallback="False"
                ).inc()
            except Exception as metric_err:
                logger.error("metrics_error_recording_failed_primary", error=str(metric_err))

            attempt_primary = False
    else:
        logger.info(
            "routing_primary_skipped",
            request_id=request_id,
            provider=primary_provider,
            circuit_state=primary_state
        )

    # 3. Fallback routing if primary was skipped or failed
    if not executed_provider:
        if not fallback_provider:
            # No fallback configured: refund estimated tokens and raise 503
            await refund_tpm_tokens(
                redis_client=redis_client,
                team_id=str(team.id),
                tier=tier,
                limit=access.rate_limit_tpm,
                refund_amount=tpm_estimate
            )
            record_completed_metrics("503", primary_provider, primary_model, was_fallback)
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "provider_unavailable",
                        "message": f"Primary provider '{primary_provider}' failed and no fallback is configured.",
                        "request_id": request_id
                    }
                }
            )

        # Check fallback circuit state
        fallback_state = await fallback_cb.get_state(db)
        if fallback_state not in ("closed", "half_open"):
            await refund_tpm_tokens(
                redis_client=redis_client,
                team_id=str(team.id),
                tier=tier,
                limit=access.rate_limit_tpm,
                refund_amount=tpm_estimate
            )
            record_completed_metrics("503", fallback_provider, fallback_model, was_fallback)
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "provider_unavailable",
                        "message": "Both primary and fallback provider circuits are open.",
                        "providers_attempted": [primary_provider, fallback_provider],
                        "request_id": request_id
                    }
                }
            )

        logger.info(
            "routing_fallback_attempt",
            request_id=request_id,
            provider=fallback_provider,
            model=fallback_model,
            circuit_state=fallback_state
        )
        prov_start = time.perf_counter()
        try:
            # Single attempt on fallback provider (no retries)
            client = _get_provider_client(fallback_provider, request_id)
            content, input_tokens, output_tokens = await client.chat_completion(
                model=fallback_model,
                messages=[msg.model_dump() for msg in body.messages],
                max_tokens=body.max_tokens,
            )
            
            # Fallback Call Success!
            provider_duration += time.perf_counter() - prov_start
            executed_provider = fallback_provider
            executed_model = fallback_model
            was_fallback = True
            await fallback_cb.record_success(db)
            
        except Exception as e:
            logger.error(
                "fallback_provider_failed",
                request_id=request_id,
                provider=fallback_provider,
                error=str(e)
            )
            # Record failure against fallback circuit if it's not a rate limit
            if getattr(e, "trips_circuit", True):
                await fallback_cb.record_failure(str(e), db)
            
            # Both failed: refund estimated tokens and raise 503
            await refund_tpm_tokens(
                redis_client=redis_client,
                team_id=str(team.id),
                tier=tier,
                limit=access.rate_limit_tpm,
                refund_amount=tpm_estimate
            )
            record_completed_metrics("503", fallback_provider, fallback_model, was_fallback)
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "provider_unavailable",
                        "message": f"Both primary and fallback providers failed. Primary: {primary_provider}, Fallback: {fallback_provider}.",
                        "providers_attempted": [primary_provider, fallback_provider],
                        "request_id": request_id
                    }
                }
            )

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    cost = compute_cost(executed_provider, executed_model, input_tokens, output_tokens)
    
    # Record success metrics
    record_completed_metrics("200", executed_provider, executed_model, was_fallback, cost)

    # 4. Post-request actions: Record budget spend and Refund unused tokens
    # NOTE: Budget deduction and TPM refund writes do not need to be atomic with each other.
    await record_budget_spend(redis_client=redis_client, team_id=str(team.id), cost_usd=cost)

    actual_tpm_used = input_tokens + output_tokens
    refund_amount = tpm_estimate - actual_tpm_used
    await refund_tpm_tokens(
        redis_client=redis_client,
        team_id=str(team.id),
        tier=tier,
        limit=access.rate_limit_tpm,
        refund_amount=refund_amount
    )

    # 5. Handle response warning headers
    warning = getattr(request.state, "budget_warning", None)
    if warning:
        response.headers["X-Budget-Warning"] = warning

    # 6. Construct response
    usage = Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost
    )

    res_body = ChatCompletionResponse(
        id=request_id,
        provider=executed_provider,
        model=executed_model,
        was_fallback=was_fallback,
        content=content,
        usage=usage,
        latency_ms=latency_ms
    )

    logger.info(
        "request_success",
        request_id=request_id,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        was_fallback=was_fallback
    )
    return res_body
