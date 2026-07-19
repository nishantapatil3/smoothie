"""Background job runner.

A single asyncio loop polls the database for PENDING jobs and processes them
through: transcribing -> summarizing -> completed. Failures mark the job FAILED
with an error message. This is deliberately simple (no external queue) for
iteration 1; it can be swapped for a real queue later.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import async_session, init_db
from .models import Job, JobStage, Recording, Summary, Transcript, User
from .storage import delete_audio
from .summarize import SummaryError, get_provider
from .transcription import TranscriptionError, transcribe, warm_model
from .tiers import can_transcribe

logger = logging.getLogger("smoothie.tasks")

_running = False


async def start_worker() -> None:
    global _running
    if _running:
        return
    _running = True
    await init_db()
    # Reclaim jobs left mid-flight by a previous worker process (e.g. after a
    # --reload restart or a crash). A single-worker app means any job still in
    # TRANSCRIBING/SUMMARIZING at startup belongs to a dead process and is safe
    # to restart.
    await _recover_orphans()
    # Warm the Whisper model into MLX memory at startup. Run off the event loop
    # (blocking load) but await it so its log lines appear in order at startup.
    # Non-fatal: failures are logged and retried on first use.
    await asyncio.to_thread(warm_model)
    asyncio.create_task(_run_loop())
    logger.info("Background worker started.")


async def _recover_orphans() -> None:
    """Reset jobs stranded in a non-terminal stage back to PENDING.

    In-flight jobs from a previous worker process (killed by --reload, a crash,
    etc.) would otherwise stay stuck forever, since the worker only claims
    PENDING jobs.
    """
    async with async_session() as session:
        stmt = select(Job).where(
            Job.stage.in_([JobStage.TRANSCRIBING, JobStage.SUMMARIZING])
        )
        jobs = (await session.execute(stmt)).scalars().all()
        for job in jobs:
            job.stage = JobStage.PENDING
            job.progress = 0.0
            rec = await session.get(Recording, job.recording_id)
            if rec is not None:
                rec.status = JobStage.PENDING
        if jobs:
            await session.commit()
            logger.info("Recovered %d orphaned job(s) to PENDING.", len(jobs))


async def stop_worker() -> None:
    global _running
    _running = False


async def _run_loop() -> None:
    settings = get_settings()
    while _running:
        try:
            await _process_one()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.exception("Worker loop error: %s", exc)
        await asyncio.sleep(settings.job_poll_interval)


async def _process_one() -> None:
    async with async_session() as session:
        job = await _claim_pending(session)
        if job is None:
            return

        recording = await _load_recording(session, job.recording_id)
        if recording is None:
            # Recording vanished; clear the orphaned job so it stops polling.
            await session.delete(job)
            await session.commit()
            return

        # --- Enforce tier limits (lifetime transcription budget) ---
        # Gate compute here (not at upload) per the chosen design: uploads are
        # always accepted, but a free user whose 30-minute budget is exhausted
        # has the job marked FAILED with a clear upgrade message.
        owner = await session.get(User, recording.user_id)
        tier_value = owner.tier if owner is not None else "free"
        usage_minutes = float(owner.usage_minutes) if owner is not None else 0.0
        allowed, used, limit = can_transcribe(tier_value, usage_minutes)
        if not allowed:
            # Capture plain ids before rolling back the claim transaction; the
            # rolled-back session detaches `job`/`recording`, so accessing their
            # ORM attributes later would raise MissingGreenlet on async SQLite.
            job_id = job.id
            recording_id = recording.id
            await session.rollback()
            async with async_session() as s2:
                j = await s2.get(Job, job_id)
                if j is not None:
                    j.stage = JobStage.FAILED
                    j.error = (
                        "Transcription limit reached: your free tier allows "
                        f"{int(limit)} minutes of transcription. Upgrade to "
                        "Standard or Pro to continue."
                    )
                    rec = await s2.get(Recording, recording_id)
                    if rec is not None:
                        rec.status = JobStage.FAILED
                    await s2.commit()
            logger.info(
                "Job %s blocked: %s tier exceeded %d-min budget.",
                job_id, tier_value, int(limit),
            )
            return

        try:
            # --- Summarize-only resume ---
            # If transcription already succeeded (a transcript exists) but the
            # job is sitting in SUMMARIZING — e.g. a summary retry after a
            # failed summarization — skip re-transcribing and reuse the text.
            resume_summary = (
                job.stage == JobStage.SUMMARIZING
                and recording.transcript is not None
            )

            if resume_summary:
                summary_text_in = recording.transcript.full_text
            else:
                # --- Transcribe ---
                job.stage = JobStage.TRANSCRIBING
                recording.status = JobStage.TRANSCRIBING
                await session.commit()

                path = str(_audio_path_for(recording.filename))
                # mlx_whisper.transcribe is blocking; run it off the event loop.
                result = await asyncio.to_thread(
                    transcribe, path, recording.language
                )

                transcript = recording.transcript or Transcript(
                    recording_id=recording.id
                )
                transcript.full_text = result.text
                transcript.segments = result.segments
                session.add(transcript)
                recording.duration_sec = _duration_from(result.segments)
                job.progress = result.progress
                # Account the consumed transcription time against the lifetime
                # budget. Truncate to whole minutes so the meter is predictable.
                if owner is not None:
                    owner.usage_minutes += int((recording.duration_sec or 0) // 60)
                await session.commit()
                summary_text_in = result.text

            # --- Summarize ---
            job.stage = JobStage.SUMMARIZING
            recording.status = JobStage.SUMMARIZING
            await session.commit()

            provider = get_provider()
            summary_text = await provider.summarize(summary_text_in)

            summary = recording.summary or Summary(recording_id=recording.id)
            summary.markdown = summary_text
            summary.provider = provider.name
            session.add(summary)

            job.stage = JobStage.COMPLETED
            recording.status = JobStage.COMPLETED
            job.error = None
            await session.commit()

        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            async with async_session() as s2:
                j = await s2.get(Job, job.id)
                if j is not None:
                    j.stage = JobStage.FAILED
                    j.error = str(exc)[:2000]
                    rec = await s2.get(Recording, recording.id)
                    if rec is not None:
                        rec.status = JobStage.FAILED
                    await s2.commit()
            logger.error("Job %s failed: %s", job.id, exc)


async def _claim_pending(session: AsyncSession) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.stage.in_([JobStage.PENDING, JobStage.SUMMARIZING]))
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _load_recording(session: AsyncSession, recording_id: str) -> Recording | None:
    stmt = (
        select(Recording)
        .where(Recording.id == recording_id)
        .options(
            selectinload(Recording.transcript),
            selectinload(Recording.summary),
            selectinload(Recording.job),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _audio_path_for(filename: str) -> Path:
    from .storage import audio_path

    return audio_path(filename)


def _duration_from(segments: list[dict]) -> float | None:
    if not segments:
        return None
    return max((seg.get("end", 0.0) for seg in segments), default=0.0)


async def reprocess(recording_id: str) -> None:
    """Mark a recording's job as PENDING again (used on retry)."""
    async with async_session() as session:
        job = (
            await session.execute(
                select(Job).where(Job.recording_id == recording_id)
            )
        ).scalar_one_or_none()
        if job is None:
            return
        job.stage = JobStage.PENDING
        job.error = None
        recording = await session.get(Recording, recording_id)
        if recording is not None:
            recording.status = JobStage.PENDING
        await session.commit()


async def retry_summary(recording_id: str) -> bool:
    """Re-run only the summarization step for a recording.

    Succeeds (returns True) only when a transcript already exists, i.e.
    transcription completed but summarization failed or needs a redo. The job
    is set to SUMMARIZING so the worker resumes straight into the summary step
    (see ``_process_one``'s summarize-only branch) without re-transcribing.
    Returns False when there is no transcript to summarize yet (the caller
    should offer a full reprocess instead).
    """
    async with async_session() as session:
        recording = (
            await session.execute(
                select(Recording)
                .where(Recording.id == recording_id)
                .options(selectinload(Recording.transcript))
            )
        ).scalar_one_or_none()
        if recording is None or recording.transcript is None:
            return False
        job = (
            await session.execute(
                select(Job).where(Job.recording_id == recording_id)
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.stage = JobStage.SUMMARIZING
        job.error = None
        recording.status = JobStage.SUMMARIZING
        await session.commit()
        return True
