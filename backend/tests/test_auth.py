"""Tests for the multi-user auth backend and per-user recording scoping."""
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
    """App wired to a fresh sqlite DB (engine rebuilt per test)."""
    tmp = Path(tempfile.mkdtemp(prefix="smoothie-test-"))
    settings = config_mod.get_settings()
    settings.data_dir = tmp
    settings.auth_secret = "test-secret"
    # Rebuild the engine/session factory to use the temp DB. Tables are created
    # by the app's startup handler (which awaits init_db against this engine).
    db.engine = db.create_async_engine(settings.db_url, echo=False, future=True)
    db.async_session = db.async_sessionmaker(
        db.engine, expire_on_commit=False, class_=db.AsyncSession
    )

    with TestClient(app) as c:
        yield c


def _register(c, username="alice", password="password123"):
    return c.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    ).json()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_token(client):
    res = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    assert res.status_code == 201
    assert res.json()["token"]


def test_duplicate_username_rejected(client):
    assert _register(client, "alice")["token"]
    res = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    assert res.status_code == 409


def test_weak_password_rejected(client):
    res = client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "short"},
    )
    assert res.status_code == 422


def test_login_wrong_password_401(client):
    _register(client, "alice")
    res = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong"},
    )
    assert res.status_code == 401


def test_login_returns_token(client):
    _register(client, "alice")
    res = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert res.status_code == 200
    assert res.json()["token"]


def test_recordings_require_auth(client):
    assert client.get("/api/recordings").status_code == 401


def test_me_requires_and_accepts_token(client):
    assert client.get("/api/auth/me").status_code == 401
    token = _register(client, "alice")["token"]
    res = client.get("/api/auth/me", headers=_auth_header(token))
    assert res.status_code == 200
    assert res.json()["username"] == "alice"


def test_users_see_only_their_recordings(client):
    alice = _register(client, "alice")["token"]
    bob = _register(client, "bob")["token"]

    # Alice uploads, Bob uploads.
    a_rec = client.post(
        "/api/recordings",
        headers=_auth_header(alice),
        files={"audio": ("a.webm", b"x", "audio/webm")},
        data={"title": "Alice rec"},
    )
    b_rec = client.post(
        "/api/recordings",
        headers=_auth_header(bob),
        files={"audio": ("b.webm", b"y", "audio/wav")},
        data={"title": "Bob rec"},
    )
    assert a_rec.status_code == 201
    assert b_rec.status_code == 201

    a_list = client.get("/api/recordings", headers=_auth_header(alice)).json()
    b_list = client.get("/api/recordings", headers=_auth_header(bob)).json()
    assert [r["title"] for r in a_list] == ["Alice rec"]
    assert [r["title"] for r in b_list] == ["Bob rec"]


def test_user_cannot_read_others_recording(client):
    alice = _register(client, "alice")["token"]
    bob = _register(client, "bob")["token"]
    rec_id = client.post(
        "/api/recordings",
        headers=_auth_header(alice),
        files={"audio": ("a.webm", b"x", "audio/webm")},
    ).json()["id"]

    # Bob gets 404, not Alice's data.
    assert (
        client.get(f"/api/recordings/{rec_id}", headers=_auth_header(bob)).status_code
        == 404
    )
    assert (
        client.delete(f"/api/recordings/{rec_id}", headers=_auth_header(bob)).status_code
        == 404
    )
    # Alice can read and delete it.
    assert (
        client.get(f"/api/recordings/{rec_id}", headers=_auth_header(alice)).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/recordings/{rec_id}", headers=_auth_header(alice)
        ).status_code
        == 204
    )


def test_bad_token_rejected(client):
    assert (
        client.get("/api/recordings", headers=_auth_header("garbage.token")).status_code
        == 401
    )


def test_login_returns_refresh_token(client):
    data = _register(client, "alice")
    assert data["token"]
    assert data["refresh_token"]


def test_refresh_issues_new_pair_and_revokes_old(client):
    data = _register(client, "alice")
    old_refresh = data["refresh_token"]

    # Refresh: get a fresh access + refresh, old refresh is revoked.
    refreshed = client.post(
        "/api/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert refreshed.status_code == 200
    new_data = refreshed.json()
    # The refresh token must rotate (the security-critical invariant); the
    # access token may be identical if both calls land in the same minute.
    assert new_data["refresh_token"] != old_refresh
    assert new_data["token"]

    # The old refresh token no longer works (rotated/revoked).
    again = client.post(
        "/api/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert again.status_code == 401


def test_refresh_with_unknown_token_401(client):
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": "not-a-real-token"}
        ).status_code
        == 401
    )


def test_logout_revokes_refresh_token(client):
    data = _register(client, "alice")
    refresh = data["refresh_token"]
    access = data["token"]

    # Logout with the access token + refresh token.
    res = client.post(
        "/api/auth/logout",
        headers=_auth_header(access),
        json={"refresh_token": refresh},
    )
    assert res.status_code == 200

    # The refresh token is now dead.
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": refresh}
        ).status_code
        == 401
    )


def test_refreshed_access_token_works_for_recordings(client):
    data = _register(client, "alice")
    new_data = client.post(
        "/api/auth/refresh", json={"refresh_token": data["refresh_token"]}
    ).json()
    # The freshly-refreshed access token authorizes the API.
    res = client.get(
        "/api/recordings", headers=_auth_header(new_data["token"])
    )
    assert res.status_code == 200

