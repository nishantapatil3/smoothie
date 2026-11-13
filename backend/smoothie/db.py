"""Database engine and session management (SQLAlchemy 2.0 async)."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings
from .logging_config import get_logger

_settings = get_settings()
logger = get_logger(__name__)

engine = create_async_engine(_settings.db_url, echo=False, future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Initialize the database, creating tables and applying migrations."""
    logger.info("Initializing database")
    # Import models so they register on Base.metadata before create_all.
    from . import models  # noqa: F401

    async with engine.connect() as conn:
        # Rebuilding the legacy recordings table must not cascade-delete its
        # transcripts/jobs. SQLite only changes this pragma outside a transaction.
        foreign_keys = await conn.scalar(text("PRAGMA foreign_keys"))
        await conn.commit()
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        try:
            async with conn.begin():
                await conn.run_sync(Base.metadata.create_all)
                await _migrate_columns(conn)
        finally:
            await conn.execute(text(f"PRAGMA foreign_keys={int(bool(foreign_keys))}"))
            await conn.commit()
    logger.info("Database initialization complete")


async def _migrate_columns(conn) -> None:
    """Add columns that may be missing on an already-created database."""
    logger.debug("Checking for missing columns in jobs table")
    existing = {
        row[1]
        for row in await conn.execute(text("PRAGMA table_info(jobs)"))
    }
    if "progress" not in existing:
        logger.info("Adding progress column to jobs table")
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN progress REAL NOT NULL DEFAULT 0.0"
            )
        )
    # Job priority for queue ordering (added with priority feature)
    if "priority" not in existing:
        logger.info("Adding priority column to jobs table")
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
            )
        )
    # Job cancellation support
    if "cancelled" not in existing:
        logger.info("Adding cancelled column to jobs table")
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN cancelled BOOLEAN NOT NULL DEFAULT 0"
            )
        )
    if "cancelled_at" not in existing:
        logger.info("Adding cancelled_at column to jobs table")
        await conn.execute(
            text(
                "ALTER TABLE jobs ADD COLUMN cancelled_at DATETIME"
            )
        )

    logger.debug("Checking for missing columns in recordings table")
    existing_recordings = {
        row[1]
        for row in await conn.execute(text("PRAGMA table_info(recordings)"))
    }
    for column, definition in {
        "original_filename": "TEXT",
        "output_path": "TEXT",
        "artifacts_dirty": "BOOLEAN NOT NULL DEFAULT 0",
        "artifact_revision": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if column not in existing_recordings:
            await conn.execute(text(f"ALTER TABLE recordings ADD COLUMN {column} {definition}"))
    # LLM-generated short title (3-4 words) added with the auto-title feature.
    if "generated_title" not in existing_recordings:
        logger.info("Adding generated_title column to recordings table")
        await conn.execute(
            text(
                "ALTER TABLE recordings ADD COLUMN generated_title "
                "VARCHAR(255)"
            )
        )

    if "user_id" in existing_recordings:
        await _remove_recording_ownership(conn)


async def _remove_recording_ownership(conn) -> None:
    """Preserve all recordings and dependent rows while removing the owner FK.

    Old account/session tables are left untouched as inactive legacy data.
    Fresh databases never create them. Rebuilding is necessary because the old
    NOT NULL foreign key would prevent uploads without an account.
    """
    from .models import Recording

    logger.info("Migrating recordings to the shared library")
    replacement = Recording.__table__.to_metadata(
        MetaData(), name="recordings_without_accounts"
    )
    # If a prior attempt stopped before copying, the source is still authoritative.
    await conn.execute(text("DROP TABLE IF EXISTS recordings_without_accounts"))
    await conn.run_sync(lambda sync: replacement.create(sync))
    columns = ", ".join(f'"{column.name}"' for column in Recording.__table__.columns)
    await conn.execute(text(
        f"INSERT INTO recordings_without_accounts ({columns}) "
        f"SELECT {columns} FROM recordings"
    ))
    await conn.execute(text("DROP TABLE recordings"))
    await conn.execute(text("ALTER TABLE recordings_without_accounts RENAME TO recordings"))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session."""
    logger.debug("Creating database session")
    async with async_session() as session:
        yield session
    logger.debug("Database session closed")
