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

_LOGGING_CONFIGURED: bool = False


def configure_logging(level: str | None = None) -> None:
    """Configure structured logging with structlog.

    Call once at application startup. Safe to call multiple times
    (idempotent — only the first call applies).
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    _LOGGING_CONFIGURED = True
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
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name or __name__)
    return logger


class NullObjectLogger:
    """A no-op logger for contexts where logging is explicitly disabled.

    Satisfies the ``BoundLogger`` interface (``debug/info/warning/error/critical``)
    but discards all output. Used when ``configure_logging`` has not been called
    and the caller opts out of logging for a given path (e.g. an inner package
    used as a library without a configured log root).
    """

    def debug(self, *args: Any, **kwargs: Any) -> None: ...
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def warn(self, *args: Any, **kwargs: Any) -> None: ...
    def error(self, *args: Any, **kwargs: Any) -> None: ...
    def critical(self, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, *args: Any, **kwargs: Any) -> None: ...
    def bind(self, *args: Any, **kwargs: Any) -> NullObjectLogger:
        return self

    # Context-manager style binding used by structlog
    def new(self, *args: Any, **kwargs: Any) -> NullObjectLogger:
        return self


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

        elapsed = time.monotonic() - (self._start or time.monotonic())
        self._logger.info(
            "timing.completed",
            operation=self._operation,
            elapsed_ms=round(elapsed * 1000, 2),
            **self._context,
        )


class StageTimer:
    """Accumulates per-stage wall-clock durations for run telemetry (PROD-40).

    Thread-safe for async single-threaded use. Returns a dict suitable for
    inclusion in a run manifest.
    """

    def __init__(self) -> None:
        self._stages: dict[str, float] = {}
        self._current: str | None = None
        self._current_start: float | None = None

    def start(self, stage: str) -> None:
        """Start timing a stage. If a stage is already running, finalise it."""
        import time

        self.finish()
        self._current = stage
        self._current_start = time.monotonic()

    def finish(self) -> None:
        """Finalise the current stage (if any) and record its elapsed time."""
        import time

        if self._current is not None and self._current_start is not None:
            elapsed = time.monotonic() - self._current_start
            self._stages[self._current] = round(elapsed, 3)
            self._current = None
            self._current_start = None

    @property
    def stages(self) -> dict[str, float]:
        """Immutable snapshot of completed stage durations (seconds)."""
        return dict(self._stages)

    def elapsed_ms(self, stage: str) -> float | None:
        """Return a single stage's wall-clock in milliseconds, or None."""
        s = self._stages.get(stage)
        return round(s * 1000, 2) if s is not None else None
