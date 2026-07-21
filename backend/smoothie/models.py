"""ORM models for recordings, transcripts, summaries, and jobs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

import structlog
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
from .logging_config import get_logger

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    # bcrypt hash (or other passlib-compatible scheme) of the password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Subscription tier ("free" | "standard" | "pro"). Drives usage limits and
    # feature availability; see smoothie/tiers.py.
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    # Lifetime transcription minutes consumed. Compared against the tier's cap
    # in tiers.py; never resets (free tier is a one-time budget).
    usage_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    recordings: Mapped[list["Recording"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    """Stored refresh token (only its hash is persisted, never the raw value).

    Rotated on every use: a refresh swaps in a new token and revokes the old
    one. Lets sessions be revoked (logout) and limits the blast radius of a
    leaked token.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex of the opaque refresh token issued to the client.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


class JobStage(str, enum.Enum):
    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Short LLM-generated title (3-4 words) derived from the transcript after
    # summarization. Kept separate from `title` so a name the user typed at
    # upload is never overwritten; the web UI shows this when present.
    generated_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[JobStage] = mapped_column(
        Enum(JobStage), default=JobStage.PENDING, nullable=False
    )

    owner: Mapped["User"] = relationship(back_populates="recordings")
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
    # Priority for job scheduling (higher = more urgent). Default 0.
    priority: Mapped[int] = mapped_column(default=0, nullable=False)
    # Transcription progress as a fraction (0.0-1.0); meaningful while the
    # TRANSCRIBING stage is active. Summarizing has no native progress.
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    # Job cancellation support
    cancelled: Mapped[bool] = mapped_column(default=False, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recording: Mapped[Recording] = relationship(back_populates="job")
