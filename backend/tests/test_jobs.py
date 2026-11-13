"""Recording retry, title generation, and reprocessing workflows."""
import asyncio

from smoothie import db


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
            from smoothie.summarize import SummaryResult

            return SummaryResult(title="Retry Title", summary=f"summary of: {text}")

    stub = _StubProvider()
    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "get_provider", lambda: stub)


    from smoothie.models import Job, JobStage, Recording, Transcript

    async def _setup():
        await tasks.stop_worker()
        async with db.async_session() as s:
            rec = Recording(
                id="rec_sum", filename="a.webm",
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
            f"/api/recordings/rec_sum/retry-summary"
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


    from smoothie.models import Job, JobStage, Recording

    async def _setup():
        async with db.async_session() as s:
            rec = Recording(
                id="rec_notx", filename="a.webm",
                content_type="audio/webm", status=JobStage.FAILED,
            )
            s.add(rec)
            s.add(Job(recording_id="rec_notx", stage=JobStage.FAILED, error="boom"))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        res = client.post(
            f"/api/recordings/rec_notx/retry-summary"
        )
    finally:
        loop.close()

    assert res.status_code == 409


# --- Auto-titled recording (generated_title) -------------------------------- #
def test_generated_title_set_and_surfaced(client, monkeypatch):
    """After summarization, the LLM-produced short title is stored on
    generated_title and surfaced by both the list and detail endpoints, while
    the user's original (filename) title is left untouched."""
    import asyncio

    import smoothie.tasks as tasks

    tasks.async_session = db.async_session
    loop0 = asyncio.new_event_loop()
    loop0.run_until_complete(tasks.stop_worker())
    loop0.close()

    def fake_transcribe(path, language, update_progress=None):
        from types import SimpleNamespace

        return SimpleNamespace(
            text="we discussed the quarterly roadmap",
            segments=[{"start": 0, "end": 1900, "text": "A long meeting"}], progress=1.0
        )

    class _StubProvider:
        name = "stub"

        async def summarize(self, text):
            from smoothie.summarize import SummaryResult

            return SummaryResult(
                title="Quarterly Roadmap Discussion",
                summary="summary of the roadmap",
            )

    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "get_provider", lambda: _StubProvider())


    from smoothie.models import Job, JobStage, Recording

    async def _setup():
        await tasks.stop_worker()
        async with db.async_session() as s:
            rec = Recording(
                id="rec_title", title="meeting.webm",
                filename="meeting.webm", content_type="audio/webm",
            )
            s.add(rec)
            s.add(Job(recording_id="rec_title", stage=JobStage.PENDING))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        loop.run_until_complete(tasks._process_one())
    finally:
        loop.close()

    # Detail endpoint: original title preserved, generated_title present.
    detail = client.get(
        f"/api/recordings/rec_title"
    ).json()
    assert detail["duration_sec"] == 1900  # Longer than the former free cap.
    assert detail["title"] == "meeting.webm"
    assert detail["generated_title"] == "Quarterly Roadmap Discussion"

    # List endpoint surfaces generated_title too.
    listing = client.get("/api/recordings").json()
    item = next(r for r in listing if r["id"] == "rec_title")
    assert item["generated_title"] == "Quarterly Roadmap Discussion"


def test_regenerate_title_endpoint(client, monkeypatch):
    """The regenerate-title endpoint derives a fresh short title from the
    existing summary (no re-transcribe), using the title-only provider path."""
    import asyncio

    import smoothie.tasks as tasks

    tasks.async_session = db.async_session
    loop0 = asyncio.new_event_loop()
    loop0.run_until_complete(tasks.stop_worker())
    loop0.close()

    def fake_transcribe(path, language, update_progress=None):
        from types import SimpleNamespace

        return SimpleNamespace(text="final transcript text", segments=[], progress=1.0)

    class _StubProvider:
        name = "stub"

        async def summarize(self, text):
            from smoothie.summarize import SummaryResult

            return SummaryResult(title="Old Title", summary="the stored summary")

        async def generate_title(self, summary):
            return "Fresh Regenerated Title"

    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "get_provider", lambda: _StubProvider())


    from smoothie.models import Job, JobStage, Recording, Summary

    async def _setup():
        await tasks.stop_worker()
        async with db.async_session() as s:
            rec = Recording(
                id="rec_retitle", title="meeting2.webm",
                filename="meeting2.webm", content_type="audio/webm",
            )
            s.add(rec)
            s.add(Summary(recording_id="rec_retitle", markdown="the stored summary"))
            s.add(Job(recording_id="rec_retitle", stage=JobStage.COMPLETED))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        res = client.post(
            f"/api/recordings/rec_retitle/regenerate-title",
        )
        assert res.status_code == 200
        assert res.json()["generated_title"] == "Fresh Regenerated Title"

        detail = client.get(
            f"/api/recordings/rec_retitle"
        ).json()
        assert detail["generated_title"] == "Fresh Regenerated Title"
        # User's original title is untouched.
        assert detail["title"] == "meeting2.webm"
    finally:
        loop.close()


def test_regenerate_title_409_without_summary(client):
    """Regenerate-title returns 409 when the recording has no summary yet."""
    import asyncio

    import smoothie.tasks as tasks

    tasks.async_session = db.async_session
    loop0 = asyncio.new_event_loop()
    loop0.run_until_complete(tasks.stop_worker())
    loop0.close()


    from smoothie.models import Job, JobStage, Recording

    async def _setup():
        await tasks.stop_worker()
        async with db.async_session() as s:
            rec = Recording(
                id="rec_notitle", filename="a.webm",
                content_type="audio/webm",
            )
            s.add(rec)
            s.add(Job(recording_id="rec_notitle", stage=JobStage.PENDING))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        res = client.post(
            f"/api/recordings/rec_notitle/regenerate-title",
        )
        assert res.status_code == 409
    finally:
        loop.close()


def test_reprocess_endpoint(client, monkeypatch):
    """Reprocess resets the job to PENDING so the worker will re-transcribe
    and re-summarize (full rerun)."""
    import asyncio

    import smoothie.tasks as tasks

    tasks.async_session = db.async_session
    loop0 = asyncio.new_event_loop()
    loop0.run_until_complete(tasks.stop_worker())
    loop0.close()

    def fake_transcribe(path, language, update_progress=None):
        from types import SimpleNamespace

        return SimpleNamespace(text="second transcript", segments=[], progress=1.0)

    class _StubProvider:
        name = "stub"

        async def summarize(self, text):
            from smoothie.summarize import SummaryResult

            return SummaryResult(title="Second Title", summary="second summary")

    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "get_provider", lambda: _StubProvider())


    from smoothie.models import Job, JobStage, Recording, Transcript, Summary

    async def _setup():
        await tasks.stop_worker()
        async with db.async_session() as s:
            # Simulate a completed job we want to redo
            rec = Recording(
                id="rec_reproc", title="first.webm",
                filename="first.webm", content_type="audio/webm",
            )
            s.add(rec)
            s.add(Transcript(recording_id="rec_reproc", full_text="first transcript"))
            s.add(Summary(recording_id="rec_reproc", markdown="first summary"))
            s.add(Job(recording_id="rec_reproc", stage=JobStage.COMPLETED))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_setup())
        resp = client.post(
            f"/api/recordings/rec_reproc/reprocess",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

        # Now check via the GET endpoint
        get_resp = client.get(
            f"/api/recordings/rec_reproc",
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["status"] == "pending"  # recording status
    finally:
        loop.close()


