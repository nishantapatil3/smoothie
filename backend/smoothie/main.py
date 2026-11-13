"""FastAPI application: recording upload, status, and static UI serving."""
from __future__ import annotations

import os
import uuid
import sys
from pathlib import Path

# Support both `uvicorn smoothie.main:app` and `python smoothie/main.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "smoothie"

# Use the macOS Keychain certificates, including enterprise proxy CAs, instead
# of Python's bundled CA list. This must run before importing Hugging Face/httpx.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import truststore

truststore.inject_into_ssl()

from fastapi import FastAPI, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import REPO_ROOT, get_settings
from . import __version__
from . import settings_api
from .db import async_session, get_session, init_db
from .models import FolderImport, Job, JobStage, Recording
from .schemas import (
    HealthOut,
    RecordingDetail,
    RecordingListItem,
    SegmentOut,
)
from .storage import audio_path, delete_audio, save_audio
from .tasks import regenerate_title, reprocess, retry_summary, start_worker, stop_worker
from .logging_config import configure_logging, set_request_id, clear_request_id, get_logger
from .metrics import metrics_response
from .artifacts import commit_recording, export_recording, recording_files_lock
from .watcher import watcher

logger = get_logger(__name__)


settings = get_settings()

app = FastAPI(title="Smoothie", version="0.1.0")

# Directory that may contain the built web UI (repo-root webui/dist). Optional.
UI_DIST = REPO_ROOT / "webui" / "dist"


# Request ID middleware for logging method and endpoint
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
    set_request_id(request_id)
    logger.info(f"{request.method} {request.url.path}")
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_request_id()


@app.on_event("startup")
async def _startup() -> None:
    configure_logging()
    logger.info("Application starting up")
    await init_db()
    logger.info("Database initialized")
    await watcher.start()
    await start_worker()
    logger.info("Background worker started")


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Application shutting down")
    await watcher.stop()
    await stop_worker()
    logger.info("Background worker stopped")


@app.get("/api/health", response_model=HealthOut)
async def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(
        status="ok",
        version=__version__,
        transcription_model=settings.transcription_model,
        summary_url=settings.summary_url if settings.summary_provider == "api" else "",
        summary_model=(settings.summary_model if settings.summary_provider == "api"
                       else settings.summary_codex_model or "Codex default"),
        summary_provider=settings.summary_provider,
    )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return metrics_response()


app.include_router(settings_api.router)


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

    recording_id = uuid.uuid4().hex
    filename, abs_path = save_audio(data, audio.content_type, audio.filename, recording_id)

    recording = Recording(
        id=recording_id, original_filename=audio.filename,
        output_path=str(Path(abs_path).parent),
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
    await export_recording(recording.id)
    return RecordingListItem.model_validate(recording)


@app.get("/api/recordings", response_model=list[RecordingListItem])
async def list_recordings(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[RecordingListItem]:
    result = await session.execute(
        select(Recording)
        .options(selectinload(Recording.job))
        .order_by(Recording.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    recordings = result.scalars().all()
    items = []
    for r in recordings:
        item = RecordingListItem.model_validate(
            {c.name: getattr(r, c.name) for c in Recording.__table__.columns}
        )
        if r.job is not None:
            # Use stored progress from the job; the transcription worker
            # updates job.progress as it processes segments.
            item.progress = r.job.progress
        items.append(item)
    return items


@app.get("/api/recordings/{recording_id}", response_model=RecordingDetail)
async def get_recording(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
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
        # Use stored progress from the job; the transcription worker
        # updates job.progress as it processes segments.
        item.progress = recording.job.progress
        item.error = recording.job.error

    return item


@app.delete("/api/recordings/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    async with recording_files_lock:
        delete_audio(recording.filename, recording.output_path)
        await session.execute(update(FolderImport).where(
            FolderImport.recording_id == recording_id
        ).values(recording_id=None))
        await session.delete(recording)
        await session.commit()


@app.post("/api/recordings/{recording_id}/retry-summary", status_code=200)
async def retry_recording_summary(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run only the summarization step (transcript already exists).

    Returns 409 if the recording has no transcript yet (use a full retry
    instead) and 404 if the recording does not exist.
    """
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    ok = await retry_summary(recording_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="No transcript to summarize yet. Re-run the full job instead.",
        )
    return {"ok": True, "status": "summarizing"}


@app.post("/api/recordings/{recording_id}/regenerate-title", status_code=200)
async def regenerate_recording_title(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Regenerate the short title from the recording's existing summary.

    Title-only (no re-transcribe / re-summarize). Returns 404 if the recording
    does not exist, and 409 if it has no summary yet (run a full
    summary first).
    """
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    title = await regenerate_title(recording_id)
    if title is None:
        raise HTTPException(
            status_code=409,
            detail="No summary to derive a title from yet. Generate a summary first.",
        )
    return {"ok": True, "generated_title": title}


@app.post("/api/recordings/{recording_id}/reprocess", status_code=200)
async def reprocess_recording(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reset the job to PENDING so the worker will re-transcribe and re-summarize.

    Equivalent to a full retry; title/regeneration will be recomputed from the
    new transcript. Returns 404 if the recording does not exist.
    """
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    await reprocess(recording_id)
    return {"ok": True, "status": "pending"}


@app.get("/api/recordings/{recording_id}/audio")
async def get_audio(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    recording = await session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = audio_path(recording.filename, recording.output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing.")
    return FileResponse(str(path), media_type=recording.content_type or "audio/webm")


@app.delete("/api/recordings/{recording_id}/job", status_code=200)
async def cancel_recording_job(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cancel a running job for a recording.

    Only works if the job is in PENDING, TRANSCRIBING, or SUMMARIZING stage.
    Returns the updated job status.
    """
    recording = await session.get(
        Recording, recording_id, options=[selectinload(Recording.job)]
    )
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")

    job = recording.job
    if job is None:
        raise HTTPException(status_code=404, detail="No job associated with this recording.")

    if job.stage not in (JobStage.PENDING, JobStage.TRANSCRIBING, JobStage.SUMMARIZING):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel job in {job.stage.value} stage.",
        )

    from datetime import datetime, timezone
    job.cancelled = True
    job.cancelled_at = datetime.now(timezone.utc)
    job.stage = JobStage.CANCELLED
    job.error = "Job cancelled by user"
    recording.status = JobStage.CANCELLED
    await commit_recording(session, recording)

    return {"ok": True, "status": "cancelled", "message": "Job cancelled successfully"}


@app.get("/api/recordings/{recording_id}/events")
async def recording_events(
    recording_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Server-Sent Events endpoint for real-time job progress updates.

    Streams job progress updates as they happen. The client receives events
    with the current job stage, progress percentage, and any error messages.

    """
    recording = (
        await session.execute(
            select(Recording)
            .where(Recording.id == recording_id)
            .options(selectinload(Recording.job))
        )
    ).scalar_one_or_none()
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")

    job = recording.job
    if job is None:
        raise HTTPException(status_code=404, detail="No job associated with this recording.")

    async def event_generator():
        import asyncio
        last_progress = -1.0
        last_stage = None
        last_error = None
        last_title = None

        while True:
            # Refresh job from DB
            async with async_session() as s:
                j = await s.get(Job, job.id)
                if j is None:
                    break
                current_progress = j.progress
                current_stage = j.stage.value if j.stage else None
                current_error = j.error
                # The generated title appears when summarization completes; fetch
                # it so the list view can update its label live without a reload.
                rec = await s.get(Recording, job.recording_id)
                current_title = rec.generated_title if rec is not None else None

                # Only send if something changed
                if (current_progress != last_progress or
                    current_stage != last_stage or
                    current_error != last_error or
                    current_title != last_title):
                    import json
                    data = {
                        "stage": current_stage,
                        "progress": current_progress,
                        "error": current_error,
                        "generated_title": current_title,
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                    last_progress = current_progress
                    last_stage = current_stage
                    last_error = current_error
                    last_title = current_title

                # Stop if job is in a terminal state
                if current_stage in ("completed", "failed", "cancelled"):
                    break

            await asyncio.sleep(0.5)  # Poll every 500ms

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# --- Static UI serving (production: build the webui into webui/dist) ---
if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_ui(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        index = UI_DIST / "index.html"
        requested = UI_DIST / full_path
        if full_path and requested.exists() and requested.is_file():
            return FileResponse(str(requested))
        return FileResponse(str(index))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
