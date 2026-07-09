"""Ingest Port — abstract interface for document ingestion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class IngestPort(ABC):
    """Port for ingesting documents from various formats."""

    @abstractmethod
    async def ingest(self, source: Path | str) -> str:
        """Ingest a document and return cleaned text."""
        ...
