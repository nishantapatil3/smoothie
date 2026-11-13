"""An account-free deployment exposes the complete recording workflow."""
import json
import wave

import pytest

from sqlalchemy import select

from smoothie import db, main
from smoothie.models import Job, JobStage, Recording


def upload(client, data=b"recorded audio", title="Meeting"):
    response = client.post("/api/recordings", files={
        "audio": ("meeting.webm", data, "audio/webm"),
    }, data={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_upload_list_detail_audio_and_delete_without_login(client):
    from fastapi.testclient import TestClient

    first = upload(client)
    second = upload(client, title="Another meeting")
    # A separate browser/client sees the same deployment-wide library.
    other = TestClient(main.app)
    listing = other.get("/api/recordings").json()
    assert {r["id"] for r in listing} == {first, second}
    detail = other.get(f"/api/recordings/{first}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Meeting"
    assert "user_id" not in detail.json()
    audio = other.get(f"/api/recordings/{first}/audio")
    assert audio.status_code == 200
    assert audio.content == b"recorded audio"
    assert other.delete(f"/api/recordings/{first}").status_code == 204
    assert client.get(f"/api/recordings/{first}").status_code == 404


def test_empty_upload_rejected_without_login(client):
    assert client.post("/api/recordings", files={
        "audio": ("empty.wav", b"", "audio/wav")
    }).status_code == 400


def test_recording_longer_than_former_cap_is_accepted(client, tmp_path):
    # Valid 31-minute PCM WAV. No inference is run by the idle test worker.
    clip = tmp_path / "long.wav"
    with wave.open(str(clip), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * (31 * 60 * 8000))
    response = client.post("/api/recordings", files={
        "audio": ("long.wav", clip.read_bytes(), "audio/wav")
    })
    assert response.status_code == 201


def test_cancel_pending_job_without_login(client):
    recording_id = upload(client)
    response = client.delete(f"/api/recordings/{recording_id}/job")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(f"/api/recordings/{recording_id}").json()["status"] == "cancelled"


def test_progress_events_without_header_or_query_token(client):
    recording_id = upload(client)
    async def complete():
        async with db.async_session() as session:
            recording = await session.get(Recording, recording_id)
            job = (await session.execute(select(Job).where(Job.recording_id == recording_id))).scalar_one()
            recording.status = JobStage.COMPLETED
            job.stage = JobStage.COMPLETED
            job.progress = 1.0
            await session.commit()
    client.portal.call(complete)
    response = client.get(f"/api/recordings/{recording_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    event = json.loads(response.text.split("data: ", 1)[1].strip())
    assert event["stage"] == "completed"
    assert event["progress"] == 1.0


@pytest.mark.parametrize("suffix", ["", "/audio", "/events"])
def test_missing_recording_is_404_not_login(client, suffix):
    assert client.get(f"/api/recordings/missing{suffix}").status_code == 404


def test_auth_and_admin_endpoints_removed(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith(("/api/auth", "/api/admin")) for path in paths)
    assert client.get("/api/auth/me").status_code == 404
    assert client.get("/api/admin/users").status_code == 404
