"""Prometheus metrics for Smoothie."""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# Job metrics
jobs_total = Counter(
    "smoothie_jobs_total",
    "Total number of jobs processed",
    ["stage", "status"],  # stage: transcribing/summarizing, status: completed/failed
)

job_duration_seconds = Histogram(
    "smoothie_job_duration_seconds",
    "Job processing duration in seconds",
    ["stage"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)

# Queue metrics
queue_depth = Gauge(
    "smoothie_queue_depth",
    "Number of jobs waiting in the queue",
    ["stage"],  # pending, transcribing, summarizing
)

# Transcription metrics
transcription_duration_seconds = Histogram(
    "smoothie_transcription_duration_seconds",
    "Transcription duration in seconds",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)

transcription_chunks = Histogram(
    "smoothie_transcription_chunks",
    "Number of chunks processed per transcription",
    buckets=(1, 2, 3, 5, 10, 20, 50, 100),
)

audio_duration_seconds = Histogram(
    "smoothie_audio_duration_seconds",
    "Duration of uploaded audio files in seconds",
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600, 7200),
)

# Summarization metrics
summarization_duration_seconds = Histogram(
    "smoothie_summarization_duration_seconds",
    "Summarization duration in seconds",
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

summarization_retries = Counter(
    "smoothie_summarization_retries_total",
    "Total number of summarization retries",
)

# User metrics
active_users = Gauge(
    "smoothie_active_users",
    "Number of active users",
)

user_tier = Gauge(
    "smoothie_user_tier",
    "User tier (0=free, 1=standard, 2=pro)",
    ["username"],
)

user_usage_minutes = Gauge(
    "smoothie_user_usage_minutes",
    "User transcription usage in minutes",
    ["username", "tier"],
)

# Error metrics
errors_total = Counter(
    "smoothie_errors_total",
    "Total number of errors",
    ["component", "error_type"],
)

# HTTP metrics
http_requests_total = Counter(
    "smoothie_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "smoothie_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# Model warmup metrics
model_warmup_duration_seconds = Histogram(
    "smoothie_model_warmup_duration_seconds",
    "Model warmup duration in seconds",
    buckets=(1, 5, 10, 30, 60, 120),
)

model_warmup_success = Counter(
    "smoothie_model_warmup_success_total",
    "Model warmup success count",
)

model_warmup_failure = Counter(
    "smoothie_model_warmup_failure_total",
    "Model warmup failure count",
)


def metrics_response():
    """Generate Prometheus metrics response."""
    from fastapi import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)