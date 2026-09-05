"""Tests for CheckpointStore — atomic checkpoint persistence."""

import threading
from pathlib import Path

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
        """Verify atomic write via .tmp → rename, and no leftover .tmp file
        (the tmp filename is now unique-per-call, not the literal
        "atomic.tmp" — glob rather than check one fixed name, see DH-22)."""
        store = CheckpointStore(str(tmp_path / "atomic.json"))
        store.save({"key": "value"})
        assert (tmp_path / "atomic.json").exists()
        assert list(tmp_path.glob("*.tmp")) == []


class TestConcurrentWriters:
    """DH-21/DH-22: CheckpointStore.save()'s tmp filename used to be
    deterministic (Path.with_suffix(".tmp")), so concurrent writers to the
    same destination path collided on the identical .tmp file and
    os.replace() raised PermissionError/OSError at a 50-70% rate under
    realistic concurrent load (measured directly with a standalone repro
    harness, not just reasoned about — see docs/DEFECT_HUNT_PLAN.md DH-22).
    This is exactly the R5-handed-off DH-21 lead: a real full-suite run's
    checkpoint.save_failed log line, traced to this file.
    """

    def test_concurrent_saves_to_same_path_do_not_raise(self, tmp_path):
        """Proof-of-defect: N threads racing save() on the identical
        destination path must not raise OSError, and the store ends up in
        some valid (fully-written, not corrupted/truncated) state."""
        store = CheckpointStore(str(tmp_path / "shared.json"))
        errors: list[OSError] = []
        lock = threading.Lock()

        def worker(n: int) -> None:
            for i in range(10):
                try:
                    store.save({"thread": n, "i": i})
                except OSError as exc:
                    with lock:
                        errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == []
        loaded = store.load()
        assert loaded is not None and "thread" in loaded and "i" in loaded

    def test_two_separate_instances_same_path_do_not_raise(self, tmp_path):
        """Boundary: two independent CheckpointStore objects pointed at the
        identical path (e.g. two CLI processes sharing the default
        Outputs/ directory, per resources.py's ResourceLocator.checkpoint_path)
        must not collide either — the race is on the shared destination
        path, not on any single object's internal state."""
        path = tmp_path / "shared2.json"
        store_a = CheckpointStore(str(path))
        store_b = CheckpointStore(str(path))
        errors: list[OSError] = []
        lock = threading.Lock()

        def worker(store: CheckpointStore, label: str) -> None:
            for i in range(10):
                try:
                    store.save({"who": label, "i": i})
                except OSError as exc:
                    with lock:
                        errors.append(exc)

        t1 = threading.Thread(target=worker, args=(store_a, "a"))
        t2 = threading.Thread(target=worker, args=(store_b, "b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == []

    def test_retries_exhausted_raises_and_cleans_up_tmp(self, tmp_path, monkeypatch):
        """Boundary: if the destination stays locked past all retries, save()
        still raises OSError (never silently swallowed — the caller,
        bill_analysis_flow._save_checkpoint, is the layer that catches and
        logs it, per DH-19) and does not leave its own .tmp file behind."""
        store = CheckpointStore(str(tmp_path / "stuck.json"))

        def always_locked(_self: Path, _target: object) -> Path:
            raise PermissionError("simulated: file locked by another process")

        monkeypatch.setattr(Path, "replace", always_locked)
        monkeypatch.setattr("time.sleep", lambda _seconds: None)

        with pytest.raises(OSError):
            store.save({"key": "value"})

        assert list(tmp_path.glob("*.tmp")) == []
