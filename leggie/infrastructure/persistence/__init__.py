"""Event Store — append-only event persistence (Event Sourcing pattern).

Stores every domain event for replay, audit, and explainability.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from leggie.application.ports.event_bus import EventBusPort, EventHandler
from leggie.domain.models import Event, EventType


class InMemoryEventBus(EventBusPort):
    """In-memory event bus — handlers called synchronously on publish."""

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = {}
        self._events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self._events.append(event)
        subscribers = self._subscribers.get(event.event_type, [])
        for handler in subscribers:
            handler(event)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type] = [h for h in self._subscribers[event_type] if h != handler]

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def events_by_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def events_by_aggregate(self, aggregate_id: str) -> list[Event]:
        return [e for e in self._events if e.aggregate_id == aggregate_id]

    def clear(self) -> None:
        self._events.clear()
        self._subscribers.clear()


class JsonEventStore:
    """File-based event store — serializes events as JSONL for durability."""

    def __init__(self, path: str = "leggie_events.jsonl") -> None:
        self._path = Path(path)
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("", encoding="utf-8")

    async def append(self, event: Event) -> None:
        """Append an event to the store."""
        event_type = getattr(event.event_type, "value", event.event_type)
        data = {
            "id": str(event.id),
            "event_type": event_type,
            "aggregate_id": event.aggregate_id,
            "data": event.data,
            "timestamp": event.timestamp.isoformat(),
            "version": event.version,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def read_all(self) -> list[Event]:
        """Read all events from the store."""
        if not self._path.exists():
            return []
        events: list[Event] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    events.append(
                        Event(
                            id=data["id"],
                            event_type=EventType(data["event_type"]),
                            aggregate_id=data["aggregate_id"],
                            data=data.get("data", {}),
                            timestamp=datetime.fromisoformat(data["timestamp"]),
                            version=data.get("version", 1),
                        )
                    )
        return events

    def replay_aggregate(self, aggregate_id: str) -> list[Event]:
        """Replay all events for a specific aggregate."""
        return [e for e in self.read_all() if e.aggregate_id == aggregate_id]
