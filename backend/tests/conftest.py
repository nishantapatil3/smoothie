"""Keep API tests isolated and offline; inference has explicit integration tests."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    from smoothie import config, db, tasks, main

    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(tasks, "warm_model", lambda: None)
    # API fixtures replace db.async_session per test. Follow that replacement
    # instead of using the factory captured when these modules were imported.
    monkeypatch.setattr(tasks, "async_session", lambda: db.async_session())
    monkeypatch.setattr(main, "async_session", lambda: db.async_session())
    yield
    config.get_settings.cache_clear()
