"""CheckpointStore — atomic file-based checkpoint persistence."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class CheckpointStore:
    """Atomic file-based checkpoint store.

    Writes are atomic (write to a uniquely-named .tmp file, then rename) to
    prevent corruption on crash mid-write. The rename is retried briefly on
    OSError: concurrent writers to the same destination path (two runs
    sharing an output directory, or Windows AV/indexer scans of a
    freshly-written file) can make it transiently fail even though the
    write itself already succeeded (DH-21/DH-22).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def save(self, data: dict[str, Any]) -> None:
        """Atomically write checkpoint data."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Unique per call so two concurrent writers to the same destination
        # never open/replace the identical .tmp filename (the old
        # with_suffix(".tmp") was deterministic and shared, which is what
        # made concurrent saves collide in the first place).
        tmp = self._path.parent / f"{self._path.name}.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        for attempt in range(5):
            try:
                tmp.replace(self._path)
                return
            except OSError:
                if attempt == 4:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.05 * (attempt + 1))

    def load(self) -> dict[str, Any] | None:
        """Load checkpoint data. Returns None if no checkpoint exists."""
        if not self._path.exists():
            return None
        try:
            with open(self._path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
                return data
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self) -> None:
        """Remove the checkpoint file."""
        if self._path.exists():
            self._path.unlink(missing_ok=True)

    @property
    def exists(self) -> bool:
        return self._path.exists()
