"""Folder ingestion, recoverable copies, and generated job artifacts."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from smoothie import artifacts, config, db, tasks
from smoothie.models import FolderImport, Job, JobStage, Recording
from smoothie.storage import supported_audio
from smoothie.summarize import SummaryError, SummaryResult
from smoothie.watcher import FolderWatcher, _signature


def source_file(tmp_path, name="meeting.wav", content=b"source audio"):
    settings = config.get_settings()
    path = settings.watch_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def rows(client, model):
    async def read():
        async with db.async_session() as session:
            return (await session.execute(select(model))).scalars().all()
    return client.portal.call(read)


def import_ready(client, source, watcher=None):
    watcher = watcher or FolderWatcher()
    client.portal.call(watcher.scan_once, 0)
    client.portal.call(watcher.scan_once, 6)
    return rows(client, Recording)[-1]


def fake_provider(monkeypatch, failure=False):
    class Provider:
        name = "test"
        async def summarize(self, text):
            if failure:
                raise SummaryError("summary server unavailable")
            return SummaryResult(title="Meeting notes", summary="# Summary\nDecisions made.")
        async def generate_title(self, summary):
            return "Updated title"
    monkeypatch.setattr(tasks, "get_provider", lambda: Provider())


def fake_transcriber(monkeypatch):
    calls = []
    def transcribe(path, language=None, progress_callback=None):
        assert Path(path).is_file()
        calls.append(path)
        return SimpleNamespace(text="Hello meeting", segments=[{"start": 0, "end": 2, "text": "Hello meeting"}], progress=1)
    monkeypatch.setattr(tasks, "transcribe", transcribe)
    return calls


def test_waits_for_stability_and_keeps_original(client, tmp_path):
    source = source_file(tmp_path)
    watcher = FolderWatcher()
    client.portal.call(watcher.scan_once, 0)
    client.portal.call(watcher.scan_once, 2)
    assert rows(client, Recording) == []
    source.write_bytes(b"source audio still being written")
    client.portal.call(watcher.scan_once, 3)
    client.portal.call(watcher.scan_once, 6)
    assert rows(client, Recording) == []
    client.portal.call(watcher.scan_once, 9)
    recording = rows(client, Recording)[0]
    copied = Path(recording.output_path) / recording.filename
    assert copied.read_bytes() == source.read_bytes()
    assert source.exists()
    claim = rows(client, FolderImport)[0]
    assert claim.state == "queued"
    assert claim.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert rows(client, Job)[0].stage == JobStage.PENDING
    assert client.get(f"/api/recordings/{recording.id}/audio").content == source.read_bytes()


def test_restart_deduplication_and_new_source_version(client, tmp_path):
    source = source_file(tmp_path)
    import_ready(client, source)
    restarted = FolderWatcher()
    client.portal.call(restarted.scan_once, 0)
    client.portal.call(restarted.scan_once, 6)
    assert len(rows(client, Recording)) == 1
    source.write_bytes(b"a new version of the audio")
    client.portal.call(restarted.scan_once, 7)
    client.portal.call(restarted.scan_once, 13)
    assert len(rows(client, Recording)) == 2


def test_hidden_files_symlinks_and_nested_output_are_excluded(client, tmp_path):
    settings = config.get_settings()
    settings.output_dir = settings.watch_dir / "generated"
    source_file(tmp_path, ".hidden.wav")
    source_file(tmp_path, "notes.txt")
    source_file(tmp_path, "generated/audio.wav")
    real = source_file(tmp_path, "subfolder/real.MP3")
    (settings.watch_dir / "alias.wav").symlink_to(real)
    (settings.watch_dir / "linked-directory").symlink_to(real.parent, target_is_directory=True)
    import_ready(client, real)
    assert len(rows(client, Recording)) == 1
    assert rows(client, Recording)[0].title == "real.MP3"


def test_same_basename_produces_separate_output_directories(client, tmp_path):
    source_file(tmp_path, "first/meeting.wav")
    source_file(tmp_path, "second/meeting.wav")
    import_ready(client, None)
    recordings = rows(client, Recording)
    assert len(recordings) == 2
    assert len({r.output_path for r in recordings}) == 2


def test_recover_copy_completed_before_db_publication(client, tmp_path):
    source = source_file(tmp_path)
    info = source.stat()
    directory = config.get_settings().output_dir / "interrupted"
    directory.mkdir(parents=True)
    (directory / "audio.wav").write_bytes(source.read_bytes())
    async def seed():
        async with db.async_session() as session:
            session.add(FolderImport(id="interrupted", source_key=_signature(source, info),
                source_path=str(source), source_size=info.st_size, source_mtime_ns=info.st_mtime_ns,
                output_path=str(directory), filename="audio.wav", state="copying"))
            await session.commit()
    client.portal.call(seed)
    source.unlink()  # The durable managed copy is sufficient for recovery.
    client.portal.call(FolderWatcher().scan_once, 0)
    assert rows(client, Recording)[0].id == "interrupted"
    assert len(rows(client, Job)) == 1


def test_source_changes_during_copy_are_not_enqueued(client, tmp_path, monkeypatch):
    from smoothie import watcher as module
    source = source_file(tmp_path)
    original = module._copy
    def changing(*args):
        source.write_bytes(b"changed before copy started")
        return original(*args)
    monkeypatch.setattr(module, "_copy", changing)
    instance = FolderWatcher()
    client.portal.call(instance.scan_once, 0)
    client.portal.call(instance.scan_once, 6)
    assert rows(client, Recording) == []
    assert rows(client, FolderImport)[0].state == "changed"
    monkeypatch.setattr(module, "_copy", original)
    client.portal.call(instance.scan_once, 7)
    client.portal.call(instance.scan_once, 13)
    assert len(rows(client, Recording)) == 1


def test_end_to_end_files_summary_retry_and_source_deletion(client, tmp_path, monkeypatch):
    source = source_file(tmp_path)
    recording = import_ready(client, source)
    source.unlink()
    calls = fake_transcriber(monkeypatch)
    fake_provider(monkeypatch, failure=True)
    client.portal.call(tasks._process_one)
    output = Path(recording.output_path)
    assert (output / "transcript.txt").read_text() == "Hello meeting"
    assert json.loads((output / "transcript.json").read_text())["segments"][0]["end"] == 2
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["status"] == "failed"
    assert "summary server unavailable" in metadata["error"]
    assert not (output / "summary.md").exists()
    assert len(calls) == 1
    fake_provider(monkeypatch)
    assert client.post(f"/api/recordings/{recording.id}/retry-summary").status_code == 200
    client.portal.call(tasks._process_one)
    assert len(calls) == 1
    assert (output / "summary.md").read_text().startswith("# Summary")
    assert json.loads((output / "metadata.json").read_text())["status"] == "completed"
    assert client.post(f"/api/recordings/{recording.id}/regenerate-title").status_code == 200
    assert json.loads((output / "metadata.json").read_text())["generated_title"] == "Updated title"


def test_bad_audio_failure_is_exported(client, tmp_path, monkeypatch):
    from smoothie.transcription import TranscriptionError
    recording = import_ready(client, source_file(tmp_path))
    def invalid(*args):
        raise TranscriptionError("Could not decode audio")
    monkeypatch.setattr(tasks, "transcribe", invalid)
    client.portal.call(tasks._process_one)
    metadata = json.loads((Path(recording.output_path) / "metadata.json").read_text())
    assert metadata["status"] == "failed"
    assert "Could not decode" in metadata["error"]


def test_deleting_recording_keeps_source_and_prevents_reimport(client, tmp_path):
    source = source_file(tmp_path)
    recording = import_ready(client, source)
    assert client.delete(f"/api/recordings/{recording.id}").status_code == 204
    assert source.exists()
    assert not Path(recording.output_path).exists()
    assert rows(client, FolderImport)[0].recording_id is None
    instance = FolderWatcher()
    client.portal.call(instance.scan_once, 0)
    client.portal.call(instance.scan_once, 6)
    assert rows(client, Recording) == []


def test_copy_failure_is_retried_without_publishing_partial_audio(client, tmp_path, monkeypatch):
    from smoothie import watcher as module
    source = source_file(tmp_path)
    original = module._copy
    def disk_error(*args):
        raise OSError("disk temporarily unavailable")
    monkeypatch.setattr(module, "_copy", disk_error)
    instance = FolderWatcher()
    client.portal.call(instance.scan_once, 0)
    client.portal.call(instance.scan_once, 6)
    assert rows(client, Recording) == []
    assert rows(client, FolderImport)[0].state == "copying"
    monkeypatch.setattr(module, "_copy", original)
    client.portal.call(FolderWatcher().scan_once, 7)
    assert len(rows(client, Recording)) == 1


def test_export_failure_retries_without_retranscribing(client, tmp_path, monkeypatch):
    recording = import_ready(client, source_file(tmp_path))
    fake_transcriber(monkeypatch)
    fake_provider(monkeypatch)
    original = artifacts._write
    def disk_error(*args):
        raise OSError("disk full")
    monkeypatch.setattr(artifacts, "_write", disk_error)
    client.portal.call(tasks._process_one)
    current = rows(client, Recording)[0]
    assert current.status == JobStage.COMPLETED
    assert current.artifacts_dirty
    monkeypatch.setattr(artifacts, "_write", original)
    client.portal.call(artifacts.flush_pending_exports)
    assert not rows(client, Recording)[0].artifacts_dirty
    assert (Path(recording.output_path) / "summary.md").exists()


def test_web_uploads_also_get_generated_files(client, monkeypatch):
    fake_transcriber(monkeypatch)
    fake_provider(monkeypatch)
    response = client.post("/api/recordings", files={"audio": ("from-browser.wav", b"audio", "audio/wav")})
    assert response.status_code == 201
    recording_id = response.json()["id"]
    client.portal.call(tasks._process_one)
    detail = client.get(f"/api/recordings/{recording_id}").json()
    directory = Path(detail["output_path"])
    assert {p.name for p in directory.iterdir()} == {
        "audio.wav", "metadata.json", "transcript.txt", "transcript.json", "summary.md"
    }


def test_watcher_lifecycle_disabled_and_enabled(client, tmp_path):
    source_file(tmp_path)
    settings = config.get_settings()
    settings.watch_stable_seconds = 0
    settings.watch_poll_interval = 0.1
    instance = FolderWatcher()
    async def run():
        await instance.start()
        await asyncio.sleep(0.15)
        async with db.async_session() as session:
            assert (await session.execute(select(Recording))).scalars().all() == []
        settings.watch_enabled = True
        await asyncio.sleep(0.3)
        await instance.stop()
    client.portal.call(run)
    assert len(rows(client, Recording)) == 1


@pytest.mark.parametrize("extension", ["wav", "mp3", "m4a", "flac", "ogg", "opus", "aac", "aiff", "wma", "caf", "m4b", "webm", "mp4", "amr", "ape"])
def test_audio_formats_recognized(extension):
    assert supported_audio(Path(f"audio.{extension}"))


def test_local_paths_survive_config_save(client):
    settings = config.get_settings()
    watch, output = settings.watch_dir, settings.output_dir
    assert client.put("/api/settings", json={"watch_enabled": False}).status_code == 200
    reloaded = config.load_config()
    assert reloaded.watch_dir == watch
    assert reloaded.output_dir == output
    response = client.put("/api/settings", json={"watch_dir": "/evil", "output_dir": "/evil"})
    assert response.status_code == 422
    assert settings.watch_dir == watch
    assert settings.output_dir == output


def test_restart_resumes_saved_transcript_at_summary_stage(client, tmp_path, monkeypatch):
    recording = import_ready(client, source_file(tmp_path))
    calls = fake_transcriber(monkeypatch)
    fake_provider(monkeypatch, failure=True)
    client.portal.call(tasks._process_one)
    assert client.post(f"/api/recordings/{recording.id}/retry-summary").status_code == 200
    client.portal.call(tasks._recover_orphans)
    assert rows(client, Job)[0].stage == JobStage.SUMMARIZING
    fake_provider(monkeypatch)
    client.portal.call(tasks._process_one)
    assert len(calls) == 1
    assert rows(client, Job)[0].stage == JobStage.COMPLETED


def test_export_does_not_acknowledge_a_newer_database_revision(client, tmp_path, monkeypatch):
    from sqlalchemy import update
    recording = import_ready(client, source_file(tmp_path))
    write = artifacts._write
    async def scenario():
        loop = asyncio.get_running_loop()
        async def newer_state():
            async with db.async_session() as session:
                await session.execute(update(Recording).where(Recording.id == recording.id).values(
                    status=JobStage.FAILED, artifacts_dirty=True,
                    artifact_revision=Recording.artifact_revision + 1,
                ))
                await session.commit()
        def write_with_race(*args):
            asyncio.run_coroutine_threadsafe(newer_state(), loop).result(timeout=5)
            write(*args)
        monkeypatch.setattr(artifacts, "_write", write_with_race)
        await artifacts.export_recording(recording.id)
        async with db.async_session() as session:
            assert (await session.get(Recording, recording.id)).artifacts_dirty
        monkeypatch.setattr(artifacts, "_write", write)
        await artifacts.flush_pending_exports()
    client.portal.call(scenario)
    assert json.loads((Path(recording.output_path) / "metadata.json").read_text())["status"] == "failed"
    assert not rows(client, Recording)[0].artifacts_dirty


def test_paths_are_portable_and_cannot_watch_output():
    from smoothie.config import REPO_ROOT, Settings
    settings = Settings(watch_dir="watch", output_dir="output")
    assert settings.watch_dir == REPO_ROOT / "watch"
    assert settings.output_dir == REPO_ROOT / "output"
    with pytest.raises(ValueError, match="watch_dir must be outside"):
        Settings(watch_dir="output/inside", output_dir="output")
