"""Background job runner.

A single asyncio loop polls the database for PENDING jobs and processes them
through: transcribing -> summarizing -> completed. Failures mark the job FAILED
with an error message. This is deliberately simple (no external queue) for
iteration 1; it can be swapped for a real queue later.
"""
from __future__ import annotations

import asyncio
import logging
import time

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
from .metrics import (
    jobs_total,
    job_duration_seconds,
    queue_depth,
    transcription_duration_seconds,
    transcription_chunks,
    audio_duration_seconds,
    summarization_duration_seconds,
    summarization_retries,
    errors_total,
    model_warmup_duration_seconds,
    model_warmup_success,
    model_warmup_failure,
)

logger = logging.getLogger("smoothie.tasks")

_running = False

# In-process pub/sub for live job progress. The SSE endpoint subscribes an
# asyncio.Event per job; the worker signals it the moment it writes progress to
# the DB, so clients receive updates instantly instead of waiting for the SSE
# poll interval. Keyed by job id -> asyncio.Event (one-shot, reset each wait).
_progress_subscribers: dict[int, asyncio.Event] = {}


def subscribe_progress(job_id: int) -> asyncio.Event:
    """Register (or return) the progress event for ``job_id``."""
    ev = _progress_subscribers.get(job_id)
    if ev is None:
        ev = asyncio.Event()
        _progress_subscribers[job_id] = ev
    return ev


def notify_progress(job_id: int) -> None:
    """Wake any SSE subscriber waiting on ``job_id``."""
    ev = _progress_subscribers.get(job_id)
    if ev is not None:
        ev.set()


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
                transcribe_start = time.monotonic()
                # mlx_whisper.transcribe is blocking; run it off the event loop
                # via asyncio.to_thread. Its progress callback is invoked from
                # that worker thread, so it must be a *synchronous* function.
                # We bridge the async DB write back onto the event loop with
                # run_coroutine_threadsafe. (Earlier this was an `async` callback
                # called without await, so the coroutine was silently dropped and
                # progress never reached the UI.)
                loop = asyncio.get_running_loop()

                def update_progress(current: float, total: int | None = None) -> None:
                    # Whisper's single-pass path calls back with (current, total)
                    # while the chunked/VAD paths pass a precomputed fraction;
                    # normalize both to a 0.0-1.0 fraction.
                    progress = current / total if total else current

                    async def _persist() -> None:
                        async with async_session() as s:
                            j = await s.get(Job, job.id)
                            if j:
                                j.progress = progress
                                await s.commit()
                        notify_progress(job.id)

                    asyncio.run_coroutine_threadsafe(_persist(), loop)

                result = await asyncio.to_thread(
                    transcribe, path, recording.language, update_progress
                )
                transcribe_duration = time.monotonic() - transcribe_start

                # Check if job was cancelled during transcription
                async with async_session() as s:
                    j = await s.get(Job, job.id)
                    if j and j.cancelled:
                        logger.info("Job %s was cancelled during transcription", job.id)
                        return

                # Record transcription metrics
                transcription_duration_seconds.observe(transcribe_duration)
                transcription_chunks.observe(len(result.segments))
                if recording.duration_sec:
                    audio_duration_seconds.observe(recording.duration_sec)

                transcript = recording.transcript or Transcript(
                    recording_id=recording.id
                )
                transcript.full_text = result.text
                transcript.segments = result.segments
                session.add(transcript)
                recording.duration_sec = _duration_from(result.segments)
                job.progress = 1.0  # transcription complete
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

            summarize_start = time.monotonic()
            provider = get_provider()
            summary_text = await provider.summarize(summary_text_in)
            summarize_duration = time.monotonic() - summarize_start

            # Record summarization metrics
            summarization_duration_seconds.observe(summarize_duration)

            summary = recording.summary or Summary(recording_id=recording.id)
            summary.markdown = summary_text
            summary.provider = provider.name
            session.add(summary)

            job.stage = JobStage.COMPLETED
            recording.status = JobStage.COMPLETED
            job.error = None
            await session.commit()

            # Record successful job metrics
            jobs_total.labels(stage="total", status="completed").inc()
            job_duration_seconds.labels(stage="total").observe(
                time.monotonic() - transcribe_start if not resume_summary else summarize_duration
            )

        except SummaryError as exc:
            logger.error("Summarization failed for job %s: %s", job.id, exc)
            summarization_retries.inc()
            errors_total.labels(component="summarizer", error_type="summarization_error").inc()
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
            jobs_total.labels(stage="summarizing", status="failed").inc()
        except TranscriptionError as exc:
            logger.error("Transcription failed for job %s: %s", job.id, exc)
            errors_total.labels(component="transcriber", error_type="transcription_error").inc()
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
            jobs_total.labels(stage="transcribing", status="failed").inc()
        except Exception as exc:  # noqa: BLE001
            logger.error("Job %s failed: %s", job.id, exc)
            errors_total.labels(component="worker", error_type="other").inc()
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
            jobs_total.labels(stage="unknown", status="failed").inc()


async def _claim_pending(session: AsyncSession) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.stage.in_([JobStage.PENDING, JobStage.SUMMARIZING]))
        .with_for_update(skip_locked=True)
        .order_by(Job.priority.desc(), Job.recording_id.asc())  # FIFO within same priority
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
