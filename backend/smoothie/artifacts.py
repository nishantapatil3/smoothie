"""Export durable database results to each recording's output directory."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from . import db
from .logging_config import get_logger
from .models import FolderImport, Recording
from .storage import atomic_write

logger = get_logger(__name__)
recording_files_lock = asyncio.Lock()


async def commit_recording(session, recording):
    if recording is None:
        await session.commit()
        return
    recording_id = recording.id
    await session.flush()
    # Use DB expressions: a long-lived worker session can hold values from
    # before a previous export or a concurrent cancellation.
    await session.execute(update(Recording).where(Recording.id == recording_id).values(
        artifacts_dirty=True, artifact_revision=Recording.artifact_revision + 1,
    ).execution_options(synchronize_session=False))
    await session.commit()
    await export_recording(recording_id)


def _write(directory: Path, metadata: dict, transcript: dict | None, summary: str | None):
    if transcript is not None:
        atomic_write(directory / "transcript.txt", transcript["text"].encode("utf-8"))
        atomic_write(directory / "transcript.json", json.dumps(transcript, ensure_ascii=False, indent=2).encode("utf-8"))
    if summary is not None:
        atomic_write(directory / "summary.md", summary.encode("utf-8"))
    atomic_write(directory / "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))


async def export_recording(recording_id: str) -> bool:
    """Retryable export: keep artifacts_dirty set if writing fails.

    The DB remains authoritative, including when a summary provider is down.
    Serialize exports so an older metadata snapshot cannot overwrite a newer one.
    """
    async with recording_files_lock:
        async with db.async_session() as session:
            recording = (await session.execute(
                select(Recording).where(Recording.id == recording_id).options(
                    selectinload(Recording.transcript), selectinload(Recording.summary),
                    selectinload(Recording.job),
                )
            )).scalar_one_or_none()
            if recording is None or not recording.output_path:
                return True
            imported = (await session.execute(
                select(FolderImport).where(FolderImport.recording_id == recording_id)
            )).scalar_one_or_none()
            transcript = None if recording.transcript is None else {
                "text": recording.transcript.full_text,
                "segments": recording.transcript.segments,
            }
            metadata = {
                "id": recording.id, "original_filename": recording.original_filename,
                "audio_file": recording.filename, "title": recording.title,
                "generated_title": recording.generated_title,
                "source_path": imported.source_path if imported else None,
                "sha256": imported.sha256 if imported else None,
                "status": recording.status.value, "duration_sec": recording.duration_sec,
                "created_at": recording.created_at.isoformat(),
                "error": recording.job.error if recording.job else None,
            }
            try:
                await asyncio.to_thread(
                    _write, Path(recording.output_path), metadata, transcript,
                    recording.summary.markdown if recording.summary else None,
                )
                # Another task may have updated the job while files were being
                # written. Only acknowledge the exact revision exported here.
                await session.execute(update(Recording).where(
                    Recording.id == recording_id,
                    Recording.artifact_revision == recording.artifact_revision,
                ).values(artifacts_dirty=False))
                await session.commit()
                return True
            except OSError:
                logger.exception("Could not export recording %s; will retry.", recording_id)
                return False


async def flush_pending_exports():
    async with db.async_session() as session:
        ids = (await session.execute(select(Recording.id).where(
            Recording.artifacts_dirty.is_(True), Recording.output_path.is_not(None)
        ).limit(50))).scalars().all()
    for recording_id in ids:
        await export_recording(recording_id)
