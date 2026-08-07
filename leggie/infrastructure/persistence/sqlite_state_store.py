"""SqliteStateStore — durable StatePort adapter (PROD-06b).

Replaces ``InMemoryStateStore`` in the production container by persisting
``WorkflowState`` and stage checkpoints to SQLite (WAL mode). The port
signature is unchanged — this is a drop-in adapter.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from leggie.application.ports.state import StatePort
from leggie.domain.models import WorkflowState

_SQL_CREATE = """
CREATE TABLE IF NOT EXISTS workflow_state (
    run_id      TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_ts  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stage_checkpoint (
    run_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    data_json   TEXT NOT NULL DEFAULT '{}',
    updated_ts  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, stage)
);
"""


class SqliteStateStore(StatePort):
    """Durable workflow-state store backed by SQLite (WAL mode)."""

    def __init__(self, db_path: str | Path = "leggie.db") -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SQL_CREATE)
        self._conn.commit()

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteStateStore is closed")
        return self._conn

    # ── Workflow state ────────────────────────────────────────────

    async def get_state(self, run_id: str) -> WorkflowState | None:
        row = self._conn_or_raise().execute(
            "SELECT state_value FROM workflow_state WHERE run_id = ?", (run_id,),
        ).fetchone()
        if row is None:
            return None
        return WorkflowState(row["state_value"])  # StrEnum accepts value directly

    async def set_state(self, run_id: str, state: WorkflowState) -> None:
        self._conn_or_raise().execute(
            "INSERT OR REPLACE INTO workflow_state(run_id, state_value) VALUES (?, ?)",
            (run_id, state.value),
        )

    # ── Stage checkpoints ─────────────────────────────────────────

    async def get_checkpoint(self, run_id: str, stage: str) -> dict[str, Any] | None:
        row = self._conn_or_raise().execute(
            "SELECT data_json FROM stage_checkpoint WHERE run_id = ? AND stage = ?",
            (run_id, stage),
        ).fetchone()
        if row is None:
            return None
        # json.loads is typed -> Any; save_checkpoint only ever writes a dict,
        # so bind it to the declared shape rather than returning Any.
        checkpoint: dict[str, Any] = json.loads(row["data_json"])
        return checkpoint

    async def save_checkpoint(self, run_id: str, stage: str, data: dict[str, Any]) -> None:
        self._conn_or_raise().execute(
            "INSERT OR REPLACE INTO stage_checkpoint(run_id, stage, data_json) VALUES (?, ?, ?)",
            (run_id, stage, json.dumps(data, ensure_ascii=False)),
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
