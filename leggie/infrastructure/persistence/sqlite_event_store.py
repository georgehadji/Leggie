"""SqliteEventStore — durable EventBusPort adapter (PROD-06a).

Implements the event-bus contract over SQLite in WAL mode. Events are
appended to a monotonic sequence per run for replay/auditability.
The in-memory subscriber dispatch of ``InMemoryEventBus`` is preserved
in the same adapter — the db is the durable spine; the subscriber
dispatcher is a lightweight local concern.

Architecture note: this is a Repository + Event Sourcing adapter. It
implements ``EventBusPort`` and is swappable with ``InMemoryEventBus``
(for tests) via the DI container.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from leggie.application.ports.event_bus import EventBusPort, EventHandler
from leggie.domain.models import Event, EventType
from leggie.observability import get_logger

log = get_logger(__name__)

_SQL_CREATE = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    ts          TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS schema_version (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    version     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
"""


class SqliteEventStore(EventBusPort):
    """Durable event bus backed by SQLite (WAL mode)."""

    def __init__(self, db_path: str | Path = "leggie.db") -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._seq: dict[str, int] = {}  # run_id → next seq
        self._subscribers: dict[EventType, list[EventHandler]] = {}
        self._write_lock = asyncio.Lock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Open or create the database and apply the schema."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit mode
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SQL_CREATE)
        self._conn.commit()

    # ── Write path (single-writer, locked) ────────────────────────

    async def publish(self, event: Event) -> None:
        """Append event to the store and dispatch to subscribers."""
        if self._conn is None:
            return
        run_id = event.aggregate_id  # per plan: aggregate_id == run identity
        payload = json.dumps(event.data, ensure_ascii=False)
        ts = event.timestamp.isoformat()

        async with self._write_lock:
            seq = self._seq.get(run_id, 0)
            seq += 1
            self._seq[run_id] = seq
            self._conn.execute(
                "INSERT OR IGNORE INTO events(id, run_id, seq, event_type, aggregate_id, payload_json, ts, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.id),
                    run_id,
                    seq,
                    str(event.event_type),
                    run_id,
                    payload,
                    ts,
                    event.version,
                ),
            )

        # Dispatch to subscribers outside the write lock
        event_type = event.event_type
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        subscribers = self._subscribers.get(event_type, [])
        for handler in subscribers:
            try:
                handler(event)
            except Exception:
                log.exception(
                    "sqlite_event_store: subscriber error event=%s handler=%s",
                    str(event.event_type),
                    handler,
                )

    # ── Replay ─────────────────────────────────────────────────────

    def replay(self, run_id: str) -> list[Event]:
        """Replay all events for a given run in sequence order."""
        if self._conn is None:
            return []
        rows = self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        events: list[Event] = []
        for row in rows:
            data = json.loads(row["payload_json"]) if row["payload_json"] else {}
            events.append(
                Event(
                    id=row["id"],
                    event_type=EventType(row["event_type"]),
                    aggregate_id=row["aggregate_id"],
                    data=data,
                    timestamp=datetime.fromisoformat(row["ts"]),
                    version=row["version"],
                )
            )
        return events

    def get_seq(self, run_id: str) -> int:
        """Return the current sequence number for a run (monotonic assertion check)."""
        return self._seq.get(run_id, 0)

    # ── In-memory subscriber dispatch ─────────────────────────────

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h != handler
            ]

    def clear(self) -> None:
        """Reset subscriber list, seq, and drop rows (test helper)."""
        self._subscribers.clear()
        self._seq.clear()
        if self._conn is not None:
            self._conn.execute("DELETE FROM events")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
