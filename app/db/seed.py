import os
import secrets
import hashlib
import yaml
import asyncio
import structlog
from sqlalchemy.future import select

from app.db.session import SessionLocal
from app.models.db import Team, TeamModelAccess

logger = structlog.get_logger()

async def seed_db():
    """
    Idempotent database seeding script. Loads config.yaml and populates
    the Postgres teams and team_model_access tables.
    """
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config.yaml"
    )
    if not os.path.exists(config_path):
        logger.error("seed_failed", reason="config_not_found", path=config_path)
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    teams_data = config.get("teams", [])
    async with SessionLocal() as session:
        for team_data in teams_data:
            name = team_data.get("name")
            plaintext_key = team_data.get("api_key")
            
            if not plaintext_key:
                plaintext_key = f"key_{secrets.token_urlsafe(32)}"
                logger.info("generated_api_key_for_team", team=name, api_key=plaintext_key)

            api_key_hash = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()
            budget = float(team_data.get("monthly_budget_usd", 50.00))
            priority = team_data.get("priority_tier", "normal")

            # Check if team already exists
            result = await session.execute(select(Team).where(Team.name == name))
            team = result.scalar_one_or_none()

            if not team:
                # Create new team
                team = Team(
                    name=name,
                    api_key_hash=api_key_hash,
                    monthly_budget_usd=budget,
                    priority_tier=priority
                )
                session.add(team)
                await session.flush()  # Flush to populate team.id
                logger.info("created_team_in_seed", team_name=name, team_id=str(team.id))
            else:
                # Update existing team metadata (except api_key for safety, though key hash can be set if explicitly desired)
                team.monthly_budget_usd = budget
                team.priority_tier = priority
                logger.info("updated_team_in_seed", team_name=name, team_id=str(team.id))

            # Handle model access mappings - delete existing for refresh
            # Direct delete via table interface
            await session.execute(
                TeamModelAccess.__table__.delete().where(TeamModelAccess.team_id == team.id)
            )

            accesses = team_data.get("model_access", [])
            for access_data in accesses:
                tier = access_data.get("tier")
                primary = access_data.get("primary", {})
                fallback = access_data.get("fallback", {})
                rpm = access_data.get("rate_limit_rpm", 60)
                tpm = access_data.get("rate_limit_tpm", 50000)

                model_access = TeamModelAccess(
                    team_id=team.id,
                    logical_tier=tier,
                    primary_provider=primary.get("provider"),
                    primary_model=primary.get("model"),
                    fallback_provider=fallback.get("provider") if fallback else None,
                    fallback_model=fallback.get("model") if fallback else None,
                    rate_limit_rpm=rpm,
                    rate_limit_tpm=tpm
                )
                session.add(model_access)

        await session.commit()
    logger.info("seed_complete")

if __name__ == "__main__":
    # Setup standard logger configuration for the standalone script run
    logging_config = structlog.get_config()
    if not logging_config:
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer(),
            ]
        )
    asyncio.run(seed_db())
