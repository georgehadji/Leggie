"""Blackboard Port — abstract interface for the shared aggregation substrate.

Schema-grounded, append-only contributions (PatchBoard pattern)
with public + private agent scratch spaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from leggie.domain.models import Finding


@dataclass(frozen=True)
class BlackboardEntry:
    """A single contribution to the blackboard."""

    agent_id: str
    finding: Finding
    round: int = 0
    metadata: dict = field(default_factory=dict)


class BlackboardPort(ABC):
    """Port for the aggregation blackboard."""

    @abstractmethod
    async def post_finding(self, entry: BlackboardEntry) -> None:
        """Post a finding to the public space."""
        ...

    @abstractmethod
    async def get_findings(
        self,
        round_min: int = 0,
        agent_id: str | None = None,
    ) -> list[BlackboardEntry]:
        """Get findings from the public space, optionally filtered."""
        ...

    @abstractmethod
    async def get_all_findings(self) -> list[BlackboardEntry]:
        """Get all findings across all rounds."""
        ...

    @abstractmethod
    async def clear_round(self, round_number: int) -> None:
        """Clear findings from a specific round (for adaptive stop)."""
        ...
