"""Tests for SQLite-backed persistence adapters (Phase 4)."""

from __future__ import annotations

import asyncio

import pytest

from leggie.domain.models import Event, EventType, WorkflowState
from leggie.infrastructure.persistence.sqlite_event_store import SqliteEventStore
from leggie.infrastructure.persistence.sqlite_state_store import SqliteStateStore


class TestSqliteEventStore:
    @pytest.fixture
    def store(self, tmp_path):
        s = SqliteEventStore(tmp_path / "test.db")
        yield s
        s.close()

    @pytest.mark.asyncio
    async def test_publish_and_replay(self, store):
        ev = Event(event_type=EventType.FINDING_CREATED, aggregate_id="run-1", data={"n": 1})
        await store.publish(ev)
        repl = store.replay("run-1")
        assert len(repl) == 1
        assert repl[0].event_type == EventType.FINDING_CREATED
        assert repl[0].data == {"n": 1}

    @pytest.mark.asyncio
    async def test_separate_runs_independent(self, store):
        await store.publish(
            Event(event_type=EventType.FINDING_CREATED, aggregate_id="run-a", data={"x": 1})
        )
        await store.publish(
            Event(event_type=EventType.FINDING_CREATED, aggregate_id="run-b", data={"x": 2})
        )
        assert len(store.replay("run-a")) == 1
        assert len(store.replay("run-b")) == 1

    @pytest.mark.asyncio
    async def test_sequence_monotonic(self, store):
        """Concurrent appends maintain monotonic seq per run."""

        async def publish_n(n: int):
            await store.publish(
                Event(event_type=EventType.FINDING_CREATED, aggregate_id="run-seq", data={"n": n})
            )

        await asyncio.gather(*(publish_n(i) for i in range(20)))
        events = store.replay("run-seq")
        seqs = []
        if store._conn:
            rows = store._conn.execute(
                "SELECT seq FROM events WHERE run_id = 'run-seq' ORDER BY seq"
            ).fetchall()
            seqs = [r["seq"] for r in rows]
        assert seqs == list(range(1, 21))
        assert len(events) == 20

    @pytest.mark.asyncio
    async def test_subscriber_dispatch(self, store):
        hits: list[str] = []

        def handler(ev: Event):
            hits.append(str(ev.event_type))

        store.subscribe(EventType.FINDING_CREATED, handler)
        await store.publish(Event(event_type=EventType.FINDING_CREATED, aggregate_id="s"))
        assert len(hits) == 1
        assert "finding_created" in hits

    def test_replay_empty_run(self, store):
        assert store.replay("nonexistent") == []

    def test_get_seq(self, store):
        assert store.get_seq("run-new") == 0


class TestSqliteStateStore:
    @pytest.fixture
    def store(self, tmp_path):
        s = SqliteStateStore(tmp_path / "test.db")
        yield s
        s.close()

    @pytest.mark.asyncio
    async def test_get_set_state(self, store):
        assert await store.get_state("run-1") is None
        await store.set_state("run-1", WorkflowState.INGESTING)
        assert await store.get_state("run-1") == WorkflowState.INGESTING

    @pytest.mark.asyncio
    async def test_checkpoint_save_and_retrieve(self, store):
        data = {"round": 1, "findings": 42}
        await store.save_checkpoint("run-1", "dedup", data)
        loaded = await store.get_checkpoint("run-1", "dedup")
        assert loaded == data

    @pytest.mark.asyncio
    async def test_checkpoint_missing_returns_none(self, store):
        assert await store.get_checkpoint("run-x", "stage-x") is None
