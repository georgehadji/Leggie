"""Tests for JsonEventStore — file-based event persistence.

JsonEventStore exists as a utility for future durable event sourcing.
It is not wired into the default analysis flow runtime; events are
recorded in memory by default. Durable checkpointing is file-backed
via CheckpointStore.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from leggie.domain.models import Event, EventType
from leggie.infrastructure.persistence import JsonEventStore


class TestJsonEventStore:
    """JsonEventStore is available but not the default runtime event sink."""

    @pytest.mark.asyncio
    async def test_append_and_read_all(self, tmp_path):
        store = JsonEventStore(str(tmp_path / "events.jsonl"))
        event = Event(
            event_type=EventType.ANALYSIS_STARTED,
            aggregate_id="test-run",
            data={"file": "bill.txt"},
            timestamp=datetime(2025, 1, 1),
        )
        await store.append(event)
        events = store.read_all()
        assert len(events) == 1
        assert events[0].event_type == EventType.ANALYSIS_STARTED

    @pytest.mark.asyncio
    async def test_read_all_empty(self, tmp_path):
        store = JsonEventStore(str(tmp_path / "empty.jsonl"))
        assert store.read_all() == []

    @pytest.mark.asyncio
    async def test_replay_aggregate(self, tmp_path):
        store = JsonEventStore(str(tmp_path / "replay.jsonl"))
        await store.append(Event(
            event_type=EventType.ANALYSIS_STARTED,
            aggregate_id="run-1",
            data={},
            timestamp=datetime(2025, 1, 1),
        ))
        await store.append(Event(
            event_type=EventType.WORKFLOW_COMPLETED,
            aggregate_id="run-1",
            data={},
            timestamp=datetime(2025, 1, 1),
        ))
        await store.append(Event(
            event_type=EventType.ANALYSIS_STARTED,
            aggregate_id="run-2",
            data={},
            timestamp=datetime(2025, 1, 1),
        ))
        run1_events = store.replay_aggregate("run-1")
        assert len(run1_events) == 2
        assert all(e.aggregate_id == "run-1" for e in run1_events)
