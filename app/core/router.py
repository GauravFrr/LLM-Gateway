import structlog
from fastapi import HTTPException

from app.models.db import Team, TeamModelAccess

logger = structlog.get_logger()


class Router:
    """
    Decides model routing decisions based on team configurations and tier.
    """

    def resolve_route(self, team: Team, tier: str, request_id: str) -> TeamModelAccess:
        """
        Locates the model access config for the requested logical tier.
        Raises 400 if not configured.
        """
        access = next((a for a in team.model_accesses if a.logical_tier == tier), None)
        if not access:
            logger.warning(
                "route_resolution_failed",
                reason="tier_not_configured",
                tier=tier,
                team_id=team.id,
                request_id=request_id,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "invalid_request",
                        "message": f"Team does not have access mapping configured for tier '{tier}'.",
                        "request_id": request_id,
                    }
                },
            )
        return access


router = Router()
