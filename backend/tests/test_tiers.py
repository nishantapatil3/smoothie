"""Tests for subscription tiers, usage limits, and admin tier management."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import smoothie.config as config_mod
from smoothie import db
from smoothie.main import app
from smoothie.tiers import can_transcribe, default_tier, is_valid_tier


@pytest.fixture
def client():
    """App wired to a fresh sqlite DB (engine rebuilt per test)."""
    tmp = Path(tempfile.mkdtemp(prefix="smoothie-test-"))
    settings = config_mod.get_settings()
    settings.data_dir = tmp
    settings.auth_secret = "test-secret"
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


# --- Pure tier logic ------------------------------------------------------- #
def test_can_transcribe_free_allows_up_to_cap():
    assert can_transcribe("free", 0) == (True, 0, 30.0)
    assert can_transcribe("free", 29) == (True, 29, 30.0)
    # At the cap (30 used) the next job is blocked.
    assert can_transcribe("free", 30) == (False, 30, 30.0)
    assert can_transcribe("free", 999) == (False, 999, 30.0)


def test_can_transcribe_unlimited_tiers():
    assert can_transcribe("standard", 9999)[0] is True
    assert can_transcribe("pro", 9999)[0] is True


def test_default_tier_is_free_and_valid():
    assert default_tier() == "free"
    assert is_valid_tier("free")
    assert is_valid_tier("standard")
    assert is_valid_tier("pro")
    assert not is_valid_tier("enterprise")


# --- Auth payload ----------------------------------------------------------- #
def test_new_user_is_free_tier(client):
    token = _register(client, "alice")["token"]
    me = client.get("/api/auth/me", headers=_auth_header(token)).json()
    assert me["tier"] == "free"
    assert me["usage_minutes"] == 0
    assert me["tier_limit_minutes"] == 30
    assert me["tier_remaining_minutes"] == 30


def test_me_omits_limit_for_unlimited_tier(client):
    # Make alice an admin and bump her to pro via the admin endpoint.
    config_mod.get_settings().admin_usernames = ["alice"]
    token = _register(client, "alice")["token"]
    alice_id = client.get("/api/auth/me", headers=_auth_header(token)).json()["id"]
    res = client.put(
        f"/api/admin/users/{alice_id}/tier",
        headers=_auth_header(token),
        json={"tier": "pro"},
    )
    assert res.status_code == 200
    me = client.get("/api/auth/me", headers=_auth_header(token)).json()
    assert me["tier"] == "pro"
    assert me["tier_limit_minutes"] is None
    assert me["tier_remaining_minutes"] is None


# --- Admin gating ----------------------------------------------------------- #
def test_admin_list_requires_admin(client):
    # Reset admin list (get_settings() is lru_cacached and may carry state from
    # other tests), then a logged-in non-admin user is forbidden.
    config_mod.get_settings().admin_usernames = []
    token = _register(client, "alice")["token"]
    assert client.get("/api/admin/users", headers=_auth_header(token)).status_code == 403
    assert (
        client.put(
            "/api/admin/users/does-not-matter/tier",
            headers=_auth_header(token),
            json={"tier": "pro"},
        ).status_code
        == 403
    )


def test_admin_can_list_and_change_tier(client):
    config_mod.get_settings().admin_usernames = ["admin"]
    admin = _register(client, "admin")["token"]
    admin_id = client.get("/api/auth/me", headers=_auth_header(admin)).json()["id"]
    bob = _register(client, "bob")["token"]
    bob_id = client.get("/api/auth/me", headers=_auth_header(bob)).json()["id"]

    # Admin lists users.
    users = client.get("/api/admin/users", headers=_auth_header(admin)).json()
    tiers = {u["username"]: u["tier"] for u in users}
    assert tiers == {"admin": "free", "bob": "free"}

    # Admin upgrades bob to standard.
    res = client.put(
        f"/api/admin/users/{bob_id}/tier",
        headers=_auth_header(admin),
        json={"tier": "standard"},
    )
    assert res.status_code == 200
    assert res.json()["tier"] == "standard"

    # Invalid tier rejected.
    bad = client.put(
        f"/api/admin/users/{bob_id}/tier",
        headers=_auth_header(admin),
        json={"tier": "platinum"},
    )
    assert bad.status_code == 422


def test_non_admin_cannot_change_tier(client):
    config_mod.get_settings().admin_usernames = ["admin"]
    _register(client, "admin")
    bob = _register(client, "bob")["token"]
    bob_id = client.get("/api/auth/me", headers=_auth_header(bob)).json()["id"]
    res = client.put(
        f"/api/admin/users/{bob_id}/tier",
        headers=_auth_header(bob),
        json={"tier": "pro"},
    )
    assert res.status_code == 403


# --- Worker enforcement (free cap blocks compute) -------------------------- #
def test_worker_blocks_free_user_over_limit(client, monkeypatch):
    """Once a free user's lifetime usage hits the cap, the worker marks the
    job FAILED instead of transcribing."""
    import asyncio

    import smoothie.tasks as tasks

    # The client fixture rebuilds db.async_session against a temp engine; tasks
    # imported that name at load time, so point it at the rebuilt factory too.
    tasks.async_session = db.async_session

    # Stub transcription + summary so the job path doesn't need a model/audio.
    def fake_transcribe(path, language):
        from types import SimpleNamespace

        return SimpleNamespace(
            text="hello",
            segments=[{"start": 0.0, "end": 60.0}],
            progress=1.0,
        )

    # get_provider() builds a real provider; patch it to return a stub object.
    class _StubProvider:
        name = "stub"

        async def summarize(self, text):
            return "summary"

    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "get_provider", lambda: _StubProvider())

    token = _register(client, "alice")["token"]
    alice_id = client.get("/api/auth/me", headers=_auth_header(token)).json()["id"]

    from smoothie.models import Job, JobStage, Recording

    async def _setup():
        # Stop the live poll loop so it can't claim the job before our direct
        # call; seed a PENDING job with the user already at the 30-min cap.
        await tasks.stop_worker()
        from smoothie.models import User

        async with db.async_session() as s:
            u = await s.get(User, alice_id)
            u.usage_minutes = 30
            rec = Recording(
                id="rec_limit", user_id=alice_id, filename="a.webm",
                content_type="audio/webm",
            )
            s.add(rec)
            s.add(Job(recording_id="rec_limit", stage=JobStage.PENDING))
            await s.commit()

    async def _read():
        async with db.async_session() as s:
            job = await s.get(Job, 1)
            rec = await s.get(Recording, "rec_limit")
            return job.stage.value, job.error, rec.status.value

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        # Drive the worker path directly (avoids model warmup / live loop).
        loop.run_until_complete(tasks._process_one())
        stage, error, rec_status = loop.run_until_complete(_read())
    finally:
        loop.close()

    assert stage == "failed"
    assert rec_status == "failed"
    assert error and "limit" in error.lower()


# --- Retry summary (summarize-only resume) --------------------------------- #
def test_retry_summary_reuses_transcript(client, monkeypatch):
    """A failed summarization with an existing transcript can be retried via
    the endpoint; the worker resumes straight into summarizing (no re-transcribe)."""
    import asyncio

    import smoothie.tasks as tasks

    tasks.async_session = db.async_session
    loop0 = asyncio.new_event_loop()
    loop0.run_until_complete(tasks.stop_worker())
    loop0.close()

    transcribe_calls = []

    def fake_transcribe(path, language):
        from types import SimpleNamespace

        transcribe_calls.append(1)
        return SimpleNamespace(
            text="already transcribed", segments=[], progress=1.0
        )

    class _StubProvider:
        name = "stub"

        def __init__(self):
            self.calls = 0

        async def summarize(self, text):
            self.calls += 1
            return f"summary of: {text}"

    stub = _StubProvider()
    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "get_provider", lambda: stub)

    token = _register(client, "alice")["token"]
    alice_id = client.get("/api/auth/me", headers=_auth_header(token)).json()["id"]

    from smoothie.models import Job, JobStage, Recording, Transcript, User

    async def _setup():
        await tasks.stop_worker()
        async with db.async_session() as s:
            u = await s.get(User, alice_id)
            rec = Recording(
                id="rec_sum", user_id=alice_id, filename="a.webm",
                content_type="audio/webm", status=JobStage.FAILED,
            )
            s.add(rec)
            # Transcript already exists; summary is missing (failed summary).
            s.add(Transcript(recording_id="rec_sum", full_text="already transcribed"))
            s.add(Job(recording_id="rec_sum", stage=JobStage.FAILED, error="boom"))
            await s.commit()

    async def _read():
        from sqlalchemy import select as _select
        from sqlalchemy.orm import selectinload as _sl

        async with db.async_session() as s:
            job = await s.get(Job, 1)
            rec = (
                await s.execute(
                    _select(Recording)
                    .where(Recording.id == "rec_sum")
                    .options(_sl(Recording.summary))
                )
            ).scalar_one()
            return job.stage.value, rec.status.value, rec.summary

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        # Endpoint re-queues the job for summarization only.
        res = client.post(
            f"/api/recordings/rec_sum/retry-summary", headers=_auth_header(token)
        )
        assert res.status_code == 200
        assert res.json()["status"] == "summarizing"
        # Worker resumes: transcribe must NOT run again, summary must.
        loop.run_until_complete(tasks._process_one())
        stage, rec_status, summary = loop.run_until_complete(_read())
    finally:
        loop.close()

    assert stage == "completed"
    assert rec_status == "completed"
    assert summary is not None and "already transcribed" in summary.markdown
    assert transcribe_calls == [], "transcription should be skipped on retry"


def test_retry_summary_409_without_transcript(client):
    """Retry summary is rejected (409) when no transcript exists yet."""
    import asyncio

    token = _register(client, "alice")["token"]
    alice_id = client.get("/api/auth/me", headers=_auth_header(token)).json()["id"]

    from smoothie.models import Job, JobStage, Recording, User

    async def _setup():
        async with db.async_session() as s:
            u = await s.get(User, alice_id)
            rec = Recording(
                id="rec_notx", user_id=alice_id, filename="a.webm",
                content_type="audio/webm", status=JobStage.FAILED,
            )
            s.add(rec)
            s.add(Job(recording_id="rec_notx", stage=JobStage.FAILED, error="boom"))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        res = client.post(
            f"/api/recordings/rec_notx/retry-summary", headers=_auth_header(token)
        )
    finally:
        loop.close()

    assert res.status_code == 409


# --- Upload gate: reject audio longer than remaining budget ----------------- #
def test_upload_rejected_when_audio_exceeds_remaining(client):
    """A free user with 1 min remaining can upload a short clip but not a clip
    longer than their remaining budget (checked before queueing)."""
    import subprocess
    import asyncio

    token = _register(client, "carol")["token"]
    carol_id = client.get("/api/auth/me", headers=_auth_header(token)).json()["id"]

    # Seed carol to 29 used min -> 1 min remaining on the free tier.
    async def _seed():
        from smoothie.models import User

        async with db.async_session() as s:
            u = await s.get(User, carol_id)
            u.usage_minutes = 29
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())

        def _make_wav(seconds: int, path: str) -> bytes:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=duration={seconds}:sample_rate=16000",
                    "-t", str(seconds), path,
                ],
                check=True, capture_output=True,
            )
            return Path(path).read_bytes()

        short = _make_wav(20, "/tmp/short.wav")
        long = _make_wav(120, "/tmp/long.wav")

        # Short clip (20s) fits in the 1-min remaining budget -> 201.
        ok = client.post(
            "/api/recordings",
            headers=_auth_header(token),
            files={"audio": ("short.wav", short, "audio/wav")},
        )
        assert ok.status_code == 201, ok.text

        # Long clip (120s) exceeds the 1-min remaining budget -> 409.
        big = client.post(
            "/api/recordings",
            headers=_auth_header(token),
            files={"audio": ("long.wav", long, "audio/wav")},
        )
        assert big.status_code == 409
        assert "remaining" in big.json()["detail"].lower()
    finally:
        loop.close()
