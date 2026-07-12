"""Observability — structured logging, metrics, and tracing.

Uses structlog for structured logging with trace-id per run.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import structlog

from leggie.config.settings import get_settings


def configure_logging(level: str | None = None) -> None:
    """Configure structured logging with structlog.

    Call once at application startup.
    """
    settings = get_settings()
    log_level = level or settings.log_level

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer()
            if settings.debug
            else structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger for the given module name."""
    return structlog.get_logger(name or __name__)


# ── Trace ID context ──────────────────────────────────────────────


def _get_default_trace_id() -> str:
    return str(uuid4())


_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """Get the current trace ID, generating one if not set."""
    tid = _trace_id.get()
    if not tid:
        tid = _get_default_trace_id()
        set_trace_id(tid)
    return tid


def set_trace_id(tid: str) -> None:
    """Set the current trace ID for the context."""
    _trace_id.set(tid)


def bind_trace_id(logger: structlog.stdlib.BoundLogger) -> structlog.stdlib.BoundLogger:
    """Bind the current trace ID to a logger."""
    return logger.bind(trace_id=get_trace_id())


class Timer:
    """Simple context manager for timing operations."""

    def __init__(
        self, logger: structlog.stdlib.BoundLogger, operation: str, **context: Any
    ) -> None:
        self._logger = logger
        self._operation = operation
        self._context = context
        self._start: float | None = None

    async def __aenter__(self) -> Timer:
        import time

        self._start = time.monotonic()
        return self

    async def __aexit__(self, *args: Any) -> None:
        import time

        elapsed = time.monotonic() - self._start
        self._logger.info(
            "timing.completed",
            operation=self._operation,
            elapsed_ms=round(elapsed * 1000, 2),
            **self._context,
        )
