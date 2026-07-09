"""Ingest Adapter — implements IngestPort via IngestorFactory."""

from __future__ import annotations

from pathlib import Path

from leggie.application.ports.ingest import IngestPort


class IngestAdapter(IngestPort):
    """Concrete adapter — delegates to IngestorFactory."""

    async def ingest(self, source: Path | str) -> str:
        from leggie.infrastructure.ingest import IngestorFactory
        return await IngestorFactory.ingest(Path(source))
