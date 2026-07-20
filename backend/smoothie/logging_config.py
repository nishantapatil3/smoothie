"""Structured logging configuration with request ID tracking.

Uses structlog for JSON-structured logs with request correlation IDs.
"""
from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any, Optional

import structlog
from structlog.types import FilteringBoundLogger


# Context variable to store request ID across async boundaries
_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    """Set the current request ID in context."""
    _request_id_var.set(request_id)


def clear_request_id() -> None:
    """Clear the current request ID."""
    _request_id_var.set(None)


def add_request_id(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Add request ID to log entries if available."""
    request_id = get_request_id()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def configure_logging(json_logs: bool = False, log_level: str = "INFO") -> None:
    """Configure structlog for structured logging.

    Args:
        json_logs: If True, output JSON lines. If False, output human-readable console format.
        log_level: Minimum log level to emit.
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Set levels for noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> FilteringBoundLogger:
    """Get a structlog logger instance."""
    return structlog.get_logger(name)


# Convenience function for backward compatibility
def get_smoothie_logger(name: str = "smoothie") -> FilteringBoundLogger:
    """Get the smoothie logger with structured logging."""
    return get_logger(name)