"""Upgrades preserve recordings from former accounts and their dependent data."""
import asyncio

import pytest
from sqlalchemy import event, select, text

from smoothie import db
from smoothie.models import Job, JobStage, Recording, Summary, Transcript


@pytest.mark.parametrize("with_ownership", [True, False])
def test_existing_library_survives_migration(monkeypatch, tmp_path, with_ownership):
    engine = db.create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    # Verify the rebuild preserves children even when FK cascades are enabled.
    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(db, "engine", engine)
    factory = db.async_sessionmaker(engine, expire_on_commit=False)

    async def run():
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (id VARCHAR(32) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO users VALUES ('alice'), ('bob')"))
            owner = "user_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE," if with_ownership else ""
            # Mimic an old database, including one predating generated_title.
            await conn.execute(text(f"""CREATE TABLE recordings (
                id VARCHAR(32) PRIMARY KEY, {owner}
                title VARCHAR(255), filename VARCHAR(512) NOT NULL,
                content_type VARCHAR(128), language VARCHAR(16), duration_sec FLOAT,
                created_at DATETIME, status VARCHAR(12) NOT NULL
            )"""))
            columns = "id, title, filename, status, created_at"
            values = "'old-a', 'First', 'first.wav', 'COMPLETED', '2026-01-01'"
            if with_ownership:
                columns += ", user_id"
                values += ", 'alice'"
            await conn.execute(text(f"INSERT INTO recordings ({columns}) VALUES ({values})"))
            values = "'old-b', 'Second', 'second.wav', 'COMPLETED', '2026-01-02'"
            if with_ownership:
                values += ", 'bob'"
            await conn.execute(text(f"INSERT INTO recordings ({columns}) VALUES ({values})"))
            # Create dependent tables without triggering the app migration yet.
            await conn.run_sync(db.Base.metadata.create_all)
        async with factory() as session:
            session.add(Transcript(recording_id="old-a", full_text="Preserved transcript", segments=[{"start": 0, "end": 3, "text": "Preserved transcript"}]))
            session.add(Summary(recording_id="old-a", markdown="Preserved summary"))
            session.add(Job(recording_id="old-a", stage=JobStage.COMPLETED, progress=1.0))
            await session.commit()
        # Startup and repeat startup must both be safe.
        await db.init_db()
        await db.init_db()
        async with engine.connect() as conn:
            columns = [row[1] for row in await conn.execute(text("PRAGMA table_info(recordings)"))]
            assert "user_id" not in columns
            assert "generated_title" in columns
            assert await conn.scalar(text("PRAGMA foreign_keys")) == 1
            assert (await conn.execute(text("PRAGMA foreign_key_check"))).all() == []
        async with factory() as session:
            assert len((await session.execute(select(Recording))).scalars().all()) == 2
            assert (await session.execute(select(Transcript))).scalar_one().full_text == "Preserved transcript"
            assert (await session.execute(select(Summary))).scalar_one().markdown == "Preserved summary"
            assert (await session.execute(select(Job))).scalar_one().progress == 1.0
            # Inserts no longer need a user_id, even under enforced foreign keys.
            session.add(Recording(id="new", filename="new.wav"))
            await session.commit()
        await engine.dispose()
    asyncio.run(run())


def test_fresh_database_has_no_accounts_or_tiers(client):
    async def inspect():
        async with db.engine.connect() as conn:
            tables = {row[0] for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            assert "users" not in tables
            assert "refresh_tokens" not in tables
            assert {"recordings", "transcripts", "summaries", "jobs"} <= tables
    client.portal.call(inspect)
