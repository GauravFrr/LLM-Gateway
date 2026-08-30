import os

import pytest
import redis.asyncio as redis_async
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set MOCK_PROVIDERS and ADMIN_API_KEY for all tests before app import
os.environ.setdefault("MOCK_PROVIDERS", "True")
os.environ.setdefault("ADMIN_API_KEY", "abcd")
os.environ.setdefault("OTEL_CONSOLE_EXPORT", "False")
os.environ.setdefault("GEMINI_API_KEY", "dummy_gemini_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy_anthropic_key")
os.environ.setdefault("GROQ_API_KEY", "dummy_groq_key")

from app.config import settings  # noqa: E402
from app.core.rate_limiter import rate_limiter  # noqa: E402
from app.db.session import get_db, get_redis  # noqa: E402
from app.main import app  # noqa: E402 — must come after env setup
from app.models.db import Base  # noqa: E402

DATABASE_URL = settings.DATABASE_URL
REDIS_URL = settings.REDIS_URL


@pytest.fixture
async def db_engine():
    """Create a fresh engine + schema per test to isolate state."""
    kwargs = {"future": True, "echo": False}
    if not DATABASE_URL.startswith("sqlite"):
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 2
    engine = create_async_engine(DATABASE_URL, **kwargs)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def async_db(db_engine):
    """Fresh DB session per test."""
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def redis_client():
    """Fresh Redis client + flush before/after each test."""
    client = redis_async.Redis.from_url(REDIS_URL, decode_responses=True)
    await client.flushall()
    yield client
    await client.flushall()
    await client.close()


@pytest.fixture
async def client(async_db, redis_client):
    """httpx AsyncClient wired to the FastAPI app with DB and Redis overrides."""
    # Reset cached Lua script so rate_limiter re-registers on the current test's redis_client
    rate_limiter._script = None
    rate_limiter._refund_script = None

    async def override_get_db():
        yield async_db

    async def override_get_redis():
        yield redis_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
