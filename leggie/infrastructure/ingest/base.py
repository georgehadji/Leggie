"""Abstract base types for the ingest subsystem.

Kept in a separate module to avoid the circular import between
``ingest/__init__.py`` (factory + concrete ingestors) and
``ingest/bounded.py`` (decorator).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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
