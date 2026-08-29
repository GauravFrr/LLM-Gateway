import uuid
from datetime import datetime
from sqlalchemy import String, Numeric, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base class.
    """
    pass

class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    monthly_budget_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    priority_tier: Mapped[str] = mapped_column(String, default="normal", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    model_accesses: Mapped[list["TeamModelAccess"]] = relationship(
        "TeamModelAccess", back_populates="team", cascade="all, delete-orphan", lazy="selectin"
    )

class TeamModelAccess(Base):
    __tablename__ = "team_model_access"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    logical_tier: Mapped[str] = mapped_column(String, nullable=False)  # fast, balanced, quality
    primary_provider: Mapped[str] = mapped_column(String, nullable=False)
    primary_model: Mapped[str] = mapped_column(String, nullable=False)
    fallback_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(String, nullable=True)
    rate_limit_rpm: Mapped[int] = mapped_column(default=60, nullable=False)
    rate_limit_tpm: Mapped[int] = mapped_column(default=50000, nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="model_accesses")


class ProviderHealthEvent(Base):
    __tablename__ = "provider_health_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # circuit_opened, circuit_half_open, circuit_closed
    reason: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

