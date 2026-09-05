"""Abstract base types for the ingest subsystem.

Kept in a separate module to avoid the circular import between
``ingest/__init__.py`` (factory + concrete ingestors) and
``ingest/bounded.py`` (decorator).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path


async def run_off_loop[T](fn: Callable[[], T]) -> T:
    """Run blocking extraction work without letting it outlive the run.

    ``asyncio.to_thread`` schedules onto the loop's default
    ``ThreadPoolExecutor``, whose non-daemon workers are joined by
    ``asyncio.Runner`` at ``loop.shutdown_default_executor()`` — so when
    ``BoundedIngestor``'s ``timeout_s`` fires, the caller is freed but the
    process still waits out the abandoned work in full (measured: a 0.2 s
    timeout over 3.0 s of work still took 3.03 s to exit). That made the
    PROD-16a wall-clock cap a claim the code did not honour. A daemon thread
    is not joined at shutdown, so the cap becomes real.

    ponytail: the abandoned thread keeps burning CPU until it finishes on its
    own — what this buys is that it can no longer hold the process open or
    starve the shared executor. Upgrade path if Leggie ever grows a
    long-lived process: ProcessPoolExecutor, whose workers can genuinely be
    terminated (at the cost of per-ingest process spawn and picklable
    extractors).
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    # Settled by argument, never by closure: `except ... as exc` unbinds `exc`
    # when the block ends, so a lambda capturing it would raise NameError on
    # the loop and leave the future pending forever.
    def _set_result(value: T) -> None:
        if not future.done():  # the awaiter may already have been cancelled
            future.set_result(value)

    def _set_exception(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _runner() -> None:
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the loop below
            _hand_back(loop, _set_exception, exc)
        else:
            _hand_back(loop, _set_result, result)

    threading.Thread(target=_runner, name="leggie-ingest", daemon=True).start()
    return await future


def _hand_back(loop: asyncio.AbstractEventLoop, settle: Callable[..., None], value: object) -> None:
    """Deliver a worker's outcome to *loop*, tolerating a loop that has gone.

    An abandoned worker (BoundedIngestor timed out, run finished) outlives the
    loop it was started from; scheduling onto a closed loop raises. Nobody is
    waiting for that result any more, so dropping it is correct — but it must
    not surface as an unhandled exception in a daemon thread at exit.
    """
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(settle, value)


class IngestError(Exception):
    """Base exception for ingest failures."""


class UnsupportedFormatError(IngestError):
    """Raised when the file format is not supported."""


class InputNotFoundError(IngestError):
    """Raised when the input document does not exist or cannot be read.

    Distinct from a generic IngestError so callers can tell a permanent
    caller-side mistake (bad path) from a transient environmental failure.
    An agent driving the CLI should fail fast on this, never retry.
    """


class Ingestor(ABC):
    """Base ingestor — converts bytes/Path to cleaned text."""

    @abstractmethod
    async def ingest(self, source: Path | str) -> str:
        """Ingest a document and return cleaned text."""
        ...
