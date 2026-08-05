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

import structlog
from fastapi import FastAPI, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from . import __version__
from . import admin_api, auth, settings_api, transcription
from .db import async_session, get_session, init_db
from .models import Job, JobStage, Recording, User
from .tiers import UNLIMITED, can_transcribe
from .schemas import (
    HealthOut,
    RecordingDetail,
    RecordingListItem,
    SegmentOut,
)
from .auth import get_current_user
from .storage import audio_duration_sec, audio_path, delete_audio, save_audio
from .tasks import regenerate_title, reprocess, retry_summary, start_worker, stop_worker
from .logging_config import configure_logging, set_request_id, clear_request_id, get_logger
from .metrics import metrics_response

logger = get_logger(__name__)


settings = get_settings()

app = FastAPI(title="Smoothie", version="0.1.0")

# Directory that may contain the built web UI (repo-root webui/dist). Optional.
UI_DIST = settings.data_dir.parent.parent / "webui" / "dist"


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
    await start_worker()
    logger.info("Background worker started")


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Application shutting down")
    await stop_worker()
    logger.info("Background worker stopped")


@app.get("/api/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(
        status="ok",
        version=__version__,
        transcription_model=settings.transcription_model,
        summary_url=settings.summary_url,
        summary_model=settings.summary_model,
    )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return metrics_response()


app.include_router(auth.router)
app.include_router(settings_api.router)
app.include_router(admin_api.router)


@app.post("/api/recordings", response_model=RecordingListItem, status_code=201)
async def create_recording(
    audio: UploadFile = File(...),
    title: str | None = Form(default=None),
    language: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> RecordingListItem:
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    filename, abs_path = save_audio(data, audio.content_type)

    # Reject uploads that exceed the user's remaining transcription budget
    # before they are queued (the worker also enforces the cap, but this gives
    # immediate feedback and avoids a doomed job). Unlimited tiers skip this.
    _, _, limit = can_transcribe(user.tier, float(user.usage_minutes))
    if limit is not UNLIMITED:
        duration = audio_duration_sec(abs_path)
        if duration is not None:
            # limit/usage_minutes are in minutes; duration is in seconds.
            remaining_min = max(0.0, limit - float(user.usage_minutes))
            duration_min = duration / 60.0
            if duration_min > remaining_min:
                delete_audio(filename)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This audio is {duration_min:.1f} min but you have "
                        f"{remaining_min:.1f} min of transcription remaining on "
                        f"the {user.tier} tier. Upgrade or trim the clip to "
                        "continue."
                    ),
                )

    recording = Recording(
        user_id=user.id,
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
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[RecordingListItem]:
    result = await session.execute(
        select(Recording)
        .where(Recording.user_id == user.id)
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
    user: User = Depends(get_current_user),
) -> RecordingDetail:
    result = await session.execute(
        select(Recording)
        .where(Recording.id == recording_id, Recording.user_id == user.id)
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
    user: User = Depends(get_current_user),
) -> None:
    recording = await session.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found.")
    delete_audio(recording.filename)
    await session.delete(recording)
    await session.commit()


@app.post("/api/recordings/{recording_id}/retry-summary", status_code=200)
async def retry_recording_summary(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Re-run only the summarization step (transcript already exists).

    Returns 409 if the recording has no transcript yet (use a full retry
    instead) and 404 if it doesn't belong to the caller.
    """
    recording = await session.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
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
    user: User = Depends(get_current_user),
) -> dict:
    """Regenerate the short title from the recording's existing summary.

    Title-only (no re-transcribe / re-summarize). Returns 404 if the recording
    doesn't belong to the caller, and 409 if it has no summary yet (run a full
    summary first).
    """
    recording = await session.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
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
    user: User = Depends(get_current_user),
) -> dict:
    """Reset the job to PENDING so the worker will re-transcribe and re-summarize.

    Equivalent to a full retry; title/regeneration will be recomputed from the
    new transcript. Returns 404 if the recording doesn't belong to the caller.
    """
    recording = await session.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found.")
    await reprocess(recording_id)
    return {"ok": True, "status": "pending"}


@app.get("/api/recordings/{recording_id}/audio")
async def get_audio(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> FileResponse:
    recording = await session.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = audio_path(recording.filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing.")
    return FileResponse(str(path), media_type=recording.content_type or "audio/webm")


@app.delete("/api/recordings/{recording_id}/job", status_code=200)
async def cancel_recording_job(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Cancel a running job for a recording.

    Only works if the job is in PENDING, TRANSCRIBING, or SUMMARIZING stage.
    Returns the updated job status.
    """
    recording = await session.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
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
    await session.commit()

    return {"ok": True, "status": "cancelled", "message": "Job cancelled successfully"}


@app.get("/api/recordings/{recording_id}/events")
async def recording_events(
    recording_id: str,
    request: Request,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Server-Sent Events endpoint for real-time job progress updates.

    Streams job progress updates as they happen. The client receives events
    with the current job stage, progress percentage, and any error messages.

    Accepts auth token either as Bearer header or ?token= query parameter
    (for EventSource compatibility which doesn't support custom headers).
    """
    # Try to authenticate via token query param (for EventSource) or Bearer header
    user = None
    auth_token = token

    # If no query param token, try to extract from Authorization header
    if not auth_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]  # Remove "Bearer " prefix

    if auth_token:
        user_id = auth.verify_token(auth_token)
        if user_id:
            user = await session.get(User, user_id)

    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    recording = (
        await session.execute(
            select(Recording)
            .where(Recording.id == recording_id)
            .options(selectinload(Recording.job))
        )
    ).scalar_one_or_none()
    if recording is None or recording.user_id != user.id:
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
        index = UI_DIST / "index.html"
        requested = UI_DIST / full_path
        if full_path and requested.exists() and requested.is_file():
            return FileResponse(str(requested))
        return FileResponse(str(index))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
