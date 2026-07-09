"""CheckpointStore — atomic file-based checkpoint persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CheckpointStore:
    """Atomic file-based checkpoint store.

    Writes are atomic (write to .tmp → rename) to prevent corruption
    on crash mid-write.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def save(self, data: dict[str, Any]) -> None:
        """Atomically write checkpoint data."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self._path)

    def load(self) -> dict[str, Any] | None:
        """Load checkpoint data. Returns None if no checkpoint exists."""
        if not self._path.exists():
            return None
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self) -> None:
        """Remove the checkpoint file."""
        if self._path.exists():
            self._path.unlink(missing_ok=True)

    @property
    def exists(self) -> bool:
        return self._path.exists()
