"""Tests for CheckpointStore — atomic checkpoint persistence."""

import pytest
from leggie.infrastructure.persistence.checkpoint_store import CheckpointStore


class TestCheckpointStore:
    def test_save_and_load(self, tmp_path):
        store = CheckpointStore(str(tmp_path / "checkpoint.json"))
        data = {"state": "verifying", "findings": 12}
        store.save(data)

        loaded = store.load()
        assert loaded is not None
        assert loaded["state"] == "verifying"
        assert loaded["findings"] == 12

    def test_load_missing(self, tmp_path):
        store = CheckpointStore(str(tmp_path / "nonexistent.json"))
        assert store.load() is None

    def test_exists(self, tmp_path):
        store = CheckpointStore(str(tmp_path / "cp.json"))
        assert store.exists is False
        store.save({"ok": True})
        assert store.exists is True

    def test_delete(self, tmp_path):
        store = CheckpointStore(str(tmp_path / "cp.json"))
        store.save({"ok": True})
        store.delete()
        assert store.exists is False

    def test_atomic_write(self, tmp_path):
        """Verify atomic write via .tmp → rename."""
        store = CheckpointStore(str(tmp_path / "atomic.json"))
        store.save({"key": "value"})
        assert (tmp_path / "atomic.json").exists()
        assert not (tmp_path / "atomic.tmp").exists()
