"""Keep API tests isolated and offline; inference has explicit integration tests."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    from smoothie import config, db, tasks, main

    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    settings = config.get_settings()
    settings.watch_dir = tmp_path / "watch"
    settings.output_dir = tmp_path / "output"
    settings.watch_enabled = False
    monkeypatch.setattr(tasks, "warm_model", lambda: None)
    # API fixtures replace db.async_session per test. Follow that replacement
    # instead of using the factory captured when these modules were imported.
    monkeypatch.setattr(tasks, "async_session", lambda: db.async_session())
    monkeypatch.setattr(main, "async_session", lambda: db.async_session())
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Fresh deployment with no accounts and an idle background worker."""
    import asyncio
    from fastapi.testclient import TestClient
    from smoothie import config, db, main

    settings = config.get_settings()
    settings.data_dir = tmp_path / "data"
    engine = db.create_async_engine(settings.db_url, echo=False)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "async_session", db.async_sessionmaker(
        engine, expire_on_commit=False, class_=db.AsyncSession
    ))
    async def idle_worker():
        pass
    monkeypatch.setattr(main, "start_worker", idle_worker)
    from types import SimpleNamespace
    monkeypatch.setattr(main, "watcher", SimpleNamespace(start=idle_worker, stop=idle_worker))
    with TestClient(main.app) as test_client:
        yield test_client
    asyncio.run(engine.dispose())
