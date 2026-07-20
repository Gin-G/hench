"""Async SQLAlchemy engine/session wiring."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


def _asyncpg_url(url: str) -> str:
    """Normalise a libpq-style URL to the asyncpg driver.

    CNPG hands us ``postgresql://...`` (and sometimes ``postgres://...``);
    SQLAlchemy's async engine needs the ``+asyncpg`` driver suffix.
    """
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(
    _asyncpg_url(_settings.database_url),
    pool_pre_ping=True,
    echo=False,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session with commit/rollback handling."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
