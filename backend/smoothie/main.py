"""FastAPI application: recording upload, status, and static UI serving."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from . import transcription
from .db import get_session, init_db
from .models import Job, JobStage, Recording
from .schemas import (
    HealthOut,
    RecordingDetail,
    RecordingListItem,
    SegmentOut,
)
from .storage import audio_path, delete_audio, save_audio
from .tasks import start_worker, stop_worker

logger = logging.getLogger("smoothie")


def configure_logging() -> None:
    """Ensure Smoothie's loggers emit at INFO under uvicorn.

    Uvicorn's default config sets the root logger to WARNING and only attaches
    handlers to its own ``uvicorn.*`` loggers, so our ``smoothie.*`` INFO logs
    (e.g. model warmup) would otherwise be dropped. Add a stderr handler to the
    ``smoothie`` logger if it has none, and raise its level to INFO. This is
    idempotent and coexists with uvicorn's handlers.
    """
    smoothie_logger = logging.getLogger("smoothie")
    smoothie_logger.setLevel(logging.INFO)
    if not smoothie_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        smoothie_logger.addHandler(handler)
        # Don't propagate to root (which uvicorn leaves at WARNING); we handle it.
        smoothie_logger.propagate = False


settings = get_settings()

app = FastAPI(title="Smoothie", version="0.1.0")

# Directory that may contain the built web UI (repo-root webui/dist). Optional.
UI_DIST = settings.data_dir.parent.parent / "webui" / "dist"


@app.on_event("startup")
async def _startup() -> None:
    configure_logging()
    await init_db()
    await start_worker()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await stop_worker()


@app.get("/api/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(
        status="ok",
        transcription_model=settings.transcription_model,
        summary_url=settings.summary_url,
        summary_model=settings.summary_model,
    )


@app.post("/api/recordings", response_model=RecordingListItem, status_code=201)
async def create_recording(
    audio: UploadFile = File(...),
    title: str | None = Form(default=None),
    language: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> RecordingListItem:
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    filename, _ = save_audio(data, audio.content_type)

    recording = Recording(
        title=title or audio.filename,
        filename=filename,
        content_type=audio.content_type,
        language=language,
        status=JobStage.PENDING,
    )
    session.add(recording)
    await session.flush()
    session.add(Job(recording_id=recording.id, stage=JobStage.PENDING))
    await session.commit()
    await session.refresh(recording)
    return RecordingListItem.model_validate(recording)


@app.get("/api/recordings", response_model=list[RecordingListItem])
async def list_recordings(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[RecordingListItem]:
    result = await session.execute(
        select(Recording)
        .order_by(Recording.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    recordings = result.scalars().all()
    return [RecordingListItem.model_validate(r) for r in recordings]


@app.get("/api/recordings/{recording_id}", response_model=RecordingDetail)
async def get_recording(
    recording_id: str, session: AsyncSession = Depends(get_session)
) -> RecordingDetail:
    result = await session.execute(
        select(Recording)
        .where(Recording.id == recording_id)
        .options(
            selectinload(Recording.transcript),
            selectinload(Recording.summary),
            selectinload(Recording.job),
        )
    )
    recording = result.scalar_one_or_none()
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")

    # Build from scalar columns only. Pydantic would otherwise try to coerce
    # the ORM `summary` relationship (a Summary object) into the `summary: str`
    # field and raise; we populate relationship-derived fields manually below.
    item = RecordingDetail.model_validate(
        {c.name: getattr(recording, c.name) for c in Recording.__table__.columns}
    )
    item.filename = recording.filename
    item.content_type = recording.content_type

    if recording.transcript is not None:
        item.full_text = recording.transcript.full_text
        item.segments = [SegmentOut(**s) for s in recording.transcript.segments]

    if recording.summary is not None:
        item.summary = recording.summary.markdown
        item.summary_provider = recording.summary.provider

    if recording.job is not None:
        # While actively transcribing, read the live progress hook so polling
        # clients see the percentage advance; otherwise use the stored value.
        if recording.job.stage == JobStage.TRANSCRIBING:
            item.progress = transcription.get_progress()
        else:
            item.progress = recording.job.progress
        item.error = recording.job.error

    return item


@app.delete("/api/recordings/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    delete_audio(recording.filename)
    await session.delete(recording)
    await session.commit()


@app.get("/api/recordings/{recording_id}/audio")
async def get_audio(
    recording_id: str, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = audio_path(recording.filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing.")
    return FileResponse(str(path), media_type=recording.content_type or "audio/webm")


# --- Static UI serving (production: build the webui into webui/dist) ---
if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_ui(full_path: str) -> FileResponse:
        index = UI_DIST / "index.html"
        requested = UI_DIST / full_path
        if full_path and requested.exists() and requested.is_file():
            return FileResponse(str(requested))
        return FileResponse(str(index))
