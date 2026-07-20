"""Tests for the config.yaml-backed settings read/edit endpoints."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import smoothie.config as config_mod
from smoothie import db
from smoothie.main import app


@pytest.fixture
def client():
    """App wired to a fresh sqlite DB with an isolated config.yaml."""
    tmp = Path(tempfile.mkdtemp(prefix="smoothie-settings-"))
    settings = config_mod.get_settings()
    settings.data_dir = tmp
    settings.auth_secret = "test-secret"
    # Point the YAML loader/writer at a temp file so we don't touch repo files.
    config_mod.CONFIG_PATH = tmp / "config.yaml"
    config_mod.get_settings.cache_clear()
    db.engine = db.create_async_engine(settings.db_url, echo=False, future=True)
    db.async_session = db.async_sessionmaker(
        db.engine, expire_on_commit=False, class_=db.AsyncSession
    )
    # Ensure tables exist and migrations run
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(db.init_db())
    loop.close()

    with TestClient(app) as c:
        yield c


def _register(c, username="alice", password="password123"):
    return c.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    ).json()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_settings_requires_auth(client):
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={"summary_model": "x"}).status_code == 401


def test_get_settings_returns_current(client):
    token = _register(client)["token"]
    res = client.get("/api/settings", headers=_auth_header(token))
    assert res.status_code == 200
    body = res.json()
    assert "summary_model" in body
    assert "transcription_model" in body
    # Secrets / filesystem paths are never returned.
    assert "auth_secret" not in body
    assert "data_dir" not in body
    assert "ffmpeg_bin" not in body


def test_put_updates_and_persists(client):
    token = _register(client)["token"]
    headers = _auth_header(token)

    res = client.put(
        "/api/settings", headers=headers, json={"summary_model": "gpt-4o-mini"}
    )
    assert res.status_code == 200
    assert res.json()["summary_model"] == "gpt-4o-mini"

    # Persisted to config.yaml.
    import yaml

    data = yaml.safe_load(config_mod.CONFIG_PATH.read_text())
    assert data["summary_model"] == "gpt-4o-mini"

    # And reflected on a subsequent GET (live in-memory apply).
    again = client.get("/api/settings", headers=headers).json()
    assert again["summary_model"] == "gpt-4o-mini"


def test_put_ignores_hidden_fields(client):
    token = _register(client)["token"]
    headers = _auth_header(token)

    res = client.put(
        "/api/settings",
        headers=headers,
        json={"auth_secret": "hacked", "data_dir": "/evil", "summary_model": "ok"},
    )
    assert res.status_code == 200
    assert res.json()["summary_model"] == "ok"
    # Hidden fields are never returned (and were not applied).
    assert "auth_secret" not in res.json()
    assert "data_dir" not in res.json()


def test_put_rejects_invalid_types(client):
    token = _register(client)["token"]
    headers = _auth_header(token)
    # auth_access_ttl_minutes must be an int.
    res = client.put(
        "/api/settings", headers=headers, json={"auth_access_ttl_minutes": "not-an-int"}
    )
    assert res.status_code == 422
