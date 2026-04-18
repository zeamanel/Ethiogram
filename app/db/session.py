# app/db/session.py
from typing import AsyncGenerator
from contextlib import asynccontextmanager
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker, create_async_engine
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None
_redis_client: aioredis.Redis | None = None

def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url, pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_pre_ping=True, echo=settings.database_echo,
        )
    return _engine

def get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), class_=AsyncSession,
            expire_on_commit=False, autoflush=False, autocommit=False,
        )
    return _session_factory

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True,
            socket_connect_timeout=5, socket_timeout=5, retry_on_timeout=True,
        )
    return _redis_client

async def connect_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    logger.info("Database connection pool ready")

async def connect_redis() -> None:
    redis = await get_redis()
    await redis.ping()
    logger.info("Redis connection ready")

async def disconnect_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None

async def disconnect_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
