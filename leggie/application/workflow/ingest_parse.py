"""Shared lazy factories for the Ingest/Parse adapters.

Kept out of module-level imports so importing a workflow module never pulls
in infrastructure until an adapter is actually needed.
"""

from __future__ import annotations

from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.parse import ParsePort


def lazy_ingest_adapter() -> IngestPort:
    """Lazy factory for IngestAdapter to avoid top-level infra import."""
    from leggie.infrastructure.ingest_adapter import IngestAdapter
    return IngestAdapter()


def lazy_parse_adapter() -> ParsePort:
    """Lazy factory for ParseAdapter to avoid top-level infra import."""
    from leggie.infrastructure.parse_adapter import ParseAdapter
    return ParseAdapter()
