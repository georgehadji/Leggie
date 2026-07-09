"""State Port — abstract interface for workflow state persistence.

Enables checkpointing and resumption of bill analysis runs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.domain.models import WorkflowState


class StatePort(ABC):
    """Port for workflow state persistence."""

    @abstractmethod
    async def get_state(self, run_id: str) -> WorkflowState | None:
        """Get current workflow state for a run."""
        ...

    @abstractmethod
    async def set_state(self, run_id: str, state: WorkflowState) -> None:
        """Set workflow state for a run."""
        ...

    @abstractmethod
    async def get_checkpoint(self, run_id: str, stage: str) -> dict | None:
        """Get checkpoint data for a specific stage of a run."""
        ...

    @abstractmethod
    async def save_checkpoint(self, run_id: str, stage: str, data: dict) -> None:
        """Save checkpoint data for a specific stage."""
        ...
