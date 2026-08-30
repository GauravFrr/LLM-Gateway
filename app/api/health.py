from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/health")
async def health():
    """
    Liveness probe.
    """
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness():
    """
    Readiness probe (in Phase 1, just returns ok).
    """
    return {"status": "ready"}


@router.get("/metrics")
async def metrics():
    """
    Expose Prometheus metrics scrape endpoint.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
