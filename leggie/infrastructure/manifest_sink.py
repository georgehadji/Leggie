"""JsonManifestSink — writes a RunManifest to a JSON file.

Infrastructure adapter for the ``ManifestSinkPort``. The manifest assembly
stays in Application (RunManifestBuilder); this adapter owns the filesystem
write, so the application layer never touches paths directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from leggie.application.ports.manifest import ManifestSinkPort
from leggie.domain.manifest import RunManifest


class JsonManifestSink(ManifestSinkPort):
    """Persist a RunManifest as human-readable JSON under ``Outputs/``."""

    def __init__(self, output_dir: str | Path = "Outputs") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, manifest: RunManifest, dest: Path | None = None) -> Path:
        """Write the manifest and return the path written.

        Default filename: ``Outputs/<run_id>_manifest.json``; if no run_id,
        ``Outputs/manifest.json``.
        """
        filename = f"{manifest.run_id}_manifest.json" if manifest.run_id else "manifest.json"
        target = dest or self._output_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target
