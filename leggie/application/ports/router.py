"""Router Port — abstract interface for model routing decisions.

Routes a task to the optimal model/cascade based on task type, rules table,
and current budget state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from leggie.domain.models import ModelTier


@dataclass(frozen=True)
class RouteResult:
    """Result of a routing decision."""

    model: str
    tier: ModelTier
    max_tokens: int
    cascade_enabled: bool = True  # Can escalate to higher tier on failure


class RouterPort(ABC):
    """Port for model routing decisions."""

    @abstractmethod
    async def route(self, task_type: str, budget_remaining: float | None = None) -> RouteResult:
        """Route a task type to the optimal model/tier."""
        ...

    @abstractmethod
    async def cascade(
        self, task_type: str, current_tier: ModelTier, failure_reason: str | None = None
    ) -> RouteResult | None:
        """Attempt to escalate to the next tier in cascade.

        Returns None if already at highest tier.
        """
        ...

    @abstractmethod
    def supported_models(self) -> list[str]:
        """List all available models across all tiers."""
        ...
