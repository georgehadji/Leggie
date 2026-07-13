"""InMemoryStateStore — in-memory implementation of StatePort.

Provides a correct in-memory state persistence adapter for workflow state
and checkpoint data, satisfying the StatePort interface. The previous
binding to InMemoryEventBus was invalid because that class implements
event publish/subscribe, not state persistence.

Use for testing or single-run scenarios. Replace with a durable adapter
(e.g. SQLite or file-backed) for production multi-run resume.
"""

from __future__ import annotations

from typing import Any

from leggie.application.ports.state import StatePort
from leggie.domain.models import WorkflowState


class InMemoryStateStore(StatePort):
    """In-memory state store — stores state and checkpoint data per run ID.

    All data is ephemeral: lost on process exit. Suitable for testing and
    single-run scenarios where crash-resume across process restarts is
    handled by the CheckpointStore instead.
    """

    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}
        self._checkpoints: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_state(self, run_id: str) -> WorkflowState | None:
        """Get current workflow state for a run."""
        return self._states.get(run_id)

    async def set_state(self, run_id: str, state: WorkflowState) -> None:
        """Set workflow state for a run."""
        self._states[run_id] = state

    async def get_checkpoint(self, run_id: str, stage: str) -> dict[str, Any] | None:
        """Get checkpoint data for a specific stage of a run.

        Returns a copy of the checkpoint data to prevent accidental mutation.
        """
        data = self._checkpoints.get((run_id, stage))
        if data is not None:
            return dict(data)
        return None

    async def save_checkpoint(self, run_id: str, stage: str, data: dict[str, Any]) -> None:
        """Save checkpoint data for a specific stage."""
        self._checkpoints[(run_id, stage)] = dict(data)
