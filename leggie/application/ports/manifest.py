"""ManifestSinkPort — a new port for persisting run manifests (PROD-22).

Keeps manifest persistence behind a port so the ``RunManifestBuilder``
(Application layer) does not touch filesystem directly. Infrastructure
provides a JSON adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from leggie.domain.manifest import RunManifest


class ManifestSinkPort(ABC):
    """Persist a completed run manifest to some sink (e.g. a JSON file)."""

    @abstractmethod
    def write(self, manifest: RunManifest, dest: Path | None = None) -> Path:
        """Write the manifest and return the path it was written to."""
        ...
