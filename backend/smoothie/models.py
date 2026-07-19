"""ORM models for recordings, transcripts, summaries, and jobs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class JobStage(str, enum.Enum):
    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[JobStage] = mapped_column(
        Enum(JobStage), default=JobStage.PENDING, nullable=False
    )

    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="recording",
        uselist=False,
        cascade="all, delete-orphan",
    )
    summary: Mapped["Summary | None"] = relationship(
        back_populates="recording",
        uselist=False,
        cascade="all, delete-orphan",
    )
    job: Mapped["Job | None"] = relationship(
        back_populates="recording",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("recording_id", name="uq_transcript_recording"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False
    )
    full_text: Mapped[str] = mapped_column(Text, default="")
    # List of {"start": float, "end": float, "text": str}
    segments: Mapped[list] = mapped_column(JSON, default=list)

    recording: Mapped[Recording] = relationship(back_populates="transcript")


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (UniqueConstraint("recording_id", name="uq_summary_recording"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False
    )
    markdown: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)

    recording: Mapped[Recording] = relationship(back_populates="summary")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stage: Mapped[JobStage] = mapped_column(
        Enum(JobStage), default=JobStage.PENDING, nullable=False
    )
    # Transcription progress as a fraction (0.0-1.0); meaningful while the
    # TRANSCRIBING stage is active. Summarizing has no native progress.
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    recording: Mapped[Recording] = relationship(back_populates="job")
