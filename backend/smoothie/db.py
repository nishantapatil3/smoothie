"""Database engine and session management (SQLAlchemy 2.0 async)."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

_settings = get_settings()

engine = create_async_engine(_settings.db_url, echo=False, future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    # Import models so they register on Base.metadata before create_all.
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all only adds missing tables, not new columns on existing
        # tables. Add any columns introduced after first deploy (idempotent).
        await _migrate_columns(conn)


async def _migrate_columns(conn) -> None:
    """Add columns that may be missing on an already-created database."""
    existing = {
        row[1]
        for row in await conn.execute(text("PRAGMA table_info(jobs)"))
    }
    if "progress" not in existing:
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN progress REAL NOT NULL DEFAULT 0.0"
            )
        )
    # Job priority for queue ordering (added with priority feature)
    if "priority" not in existing:
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
            )
        )
    # Job cancellation support
    if "cancelled" not in existing:
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN cancelled BOOLEAN NOT NULL DEFAULT 0"
            )
        )
    if "cancelled_at" not in existing:
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN cancelled_at DATETIME"
            )
        )

    # The users table + recordings.user_id were added with the auth feature.
    # On a pre-auth database, recordings has no user_id column yet, and the
    # users table may not exist. Add the column (NOT NULL needs a default;
    # legacy rows keep their data but become orphaned until reassigned).
    existing_recordings = {
        row[1]
        for row in await conn.execute(text("PRAGMA table_info(recordings)"))
    }
    if "user_id" not in existing_recordings:
        await conn.execute(
            text(
                "ALTER TABLE recordings ADD COLUMN user_id "
                "VARCHAR(32) NOT NULL DEFAULT ''"
            )
        )

    # Subscription tier + lifetime usage counters added with the tiers feature.
    # On a pre-tiers database, the users table has neither column; add them
    # with safe defaults (free tier, zero usage).
    existing_users = {
        row[1]
        for row in await conn.execute(text("PRAGMA table_info(users)"))
    }
    if "tier" not in existing_users:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN tier VARCHAR(16) "
                "NOT NULL DEFAULT 'free'"
            )
        )
    if "usage_minutes" not in existing_users:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN usage_minutes "
                "INTEGER NOT NULL DEFAULT 0"
            )
        )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
