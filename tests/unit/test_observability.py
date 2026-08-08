"""Tests for observability — structlog logging and timer."""

import pytest

from leggie.observability import (
    Timer,
    bind_trace_id,
    get_logger,
    get_trace_id,
    set_trace_id,
)


class TestGetLogger:
    def test_returns_bound_logger(self):
        logger = get_logger("test")
        assert logger is not None

    def test_custom_name(self):
        logger = get_logger("leggie.test.module")
        assert logger is not None


class TestTraceContext:
    def test_get_trace_id_default(self):
        set_trace_id("test-123")
        assert get_trace_id() == "test-123"

    def test_get_trace_id_generates(self):
        set_trace_id("")  # Reset
        tid = get_trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 20

    def test_bind_trace_id(self):
        set_trace_id("trace-abc")
        logger = bind_trace_id(get_logger())
        assert "trace_id" in logger._context
        assert logger._context["trace_id"] == "trace-abc"


class TestTimer:
    @pytest.mark.asyncio
    async def test_timer_context(self, monkeypatch):
        logger = get_logger("test.timer")
        calls = []

        def capture_info(event, **kwargs):
            calls.append((event, kwargs))

        # monkeypatch rather than `logger.info = ...`: assigning over a bound
        # method is a type error, and the manual try/finally restore only ran
        # for this one attribute.
        monkeypatch.setattr(logger, "info", capture_info)

        async with Timer(logger, "test_operation", key="value"):
            pass  # Operation completes instantly

        assert len(calls) >= 1
        event, kwargs = calls[0]
        assert event == "timing.completed"
        assert kwargs["key"] == "value"
        assert "elapsed_ms" in kwargs
