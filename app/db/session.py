import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Async SQLAlchemy Engine setup
engine = create_async_engine(
    settings.DATABASE_URL, echo=False, future=True, pool_pre_ping=True, pool_size=50, max_overflow=10
)

# Session factory
SessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
)

# Async Redis Connection Pool
redis_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


async def get_db():
    """
    Dependency generator for async SQLAlchemy DB sessions.
    """
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_redis():
    """
    Dependency/Helper for async Redis connection.
    """
    client = redis.Redis(connection_pool=redis_pool)
    try:
        yield client
    finally:
        await client.close()
