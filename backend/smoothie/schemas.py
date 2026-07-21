"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import JobStage


class RecordingCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=16)


class SegmentOut(BaseModel):
    start: float
    end: float
    text: str


class RecordingListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None
    # Short LLM-generated title (3-4 words) derived after summarization. Shown
    # in the UI in preference to `title` when present, so a user's own name is
    # never overwritten.
    generated_title: str | None = None
    status: JobStage
    language: str | None
    duration_sec: float | None
    created_at: datetime
    # Transcription progress as a fraction (0.0-1.0); meaningful while the
    # recording is being transcribed. Included so the sidebar can show a bar.
    progress: float = 0.0


class RecordingDetail(RecordingListItem):
    filename: str
    content_type: str | None
    full_text: str = ""
    segments: list[SegmentOut] = Field(default_factory=list)
    summary: str | None = None
    summary_provider: str | None = None
    error: str | None = None
    progress: float = 0.0


class HealthOut(BaseModel):
    status: str
    version: str
    transcription_model: str
    summary_url: str
    summary_model: str
